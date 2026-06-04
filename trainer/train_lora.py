"""
train_lora.py
─────────────
LoRA fine-tuning สำหรับ Qwen3-8B (abstractive summarisation)
- 4 GPU, VRAM รวม 160 GB
- CUDA 12.6
- ใช้ bitsandbytes bf16 + gradient checkpointing
- หรือ unsloth ถ้าติดตั้งได้ (เร็วกว่า ~2×)

รัน:
    torchrun --nproc_per_node=4 train_lora.py
หรือ:
    accelerate launch --config_file accelerate_config.yaml train_lora.py
"""

import os
import math
import json
import argparse
from dataclasses import dataclass, field
from typing import Optional

import torch
from datasets import load_dataset
from transformers import (
    AutoTokenizer,
    AutoModelForCausalLM,
    TrainingArguments,
    DataCollatorForSeq2Seq,
    set_seed,
)
from peft import LoraConfig, get_peft_model, TaskType
from trl import SFTTrainer, SFTConfig

# ── ชื่อ token พิเศษของ Qwen3 ────────────────────────────────────────────────
THINK_START = "<think>"
THINK_END   = "</think>"


# ═════════════════════════════════════════════════════════════════════════════
# 1. CONFIG
# ═════════════════════════════════════════════════════════════════════════════
@dataclass
class ScriptArgs:
    # ── Model ─────────────────────────────────────────────────────────────
    model_name: str = "Qwen/Qwen3-8B"

    # ── Data ──────────────────────────────────────────────────────────────
    train_file: str = "data/train.jsonl"
    val_file:   str = "data/val.jsonl"

    # ── LoRA ──────────────────────────────────────────────────────────────
    lora_r:          int   = 64          # rank; เพิ่มได้ถึง 128 ถ้า VRAM เหลือ
    lora_alpha:      int   = 128         # alpha = 2 × r เป็น rule-of-thumb ที่ดี
    lora_dropout:    float = 0.05
    # target modules ของ Qwen3 (attention + MLP)
    lora_target: str = (
        "q_proj,k_proj,v_proj,o_proj,"
        "gate_proj,up_proj,down_proj"
    )

    # ── Training ──────────────────────────────────────────────────────────
    output_dir:          str   = "outputs/qwen3-8b-lora-summary"
    num_train_epochs:    int   = 3
    per_device_train_batch_size: int = 2   # 4 GPU × 2 = 8 eff. batch
    per_device_eval_batch_size:  int = 2
    gradient_accumulation_steps: int = 4  # eff. global = 4×2×2 = 16
    learning_rate:       float = 2e-4
    lr_scheduler_type:   str   = "cosine"
    warmup_ratio:        float = 0.05
    weight_decay:        float = 0.01
    max_grad_norm:       float = 1.0

    # ── Sequence ──────────────────────────────────────────────────────────
    max_seq_length: int = 4096     # ปรับลงเหลือ 2048 ถ้า VRAM ไม่พอ

    # ── Precision ─────────────────────────────────────────────────────────
    bf16: bool = True              # Qwen3 ออกแบบมาสำหรับ bfloat16
    fp16: bool = False

    # ── Misc ──────────────────────────────────────────────────────────────
    seed: int          = 42
    logging_steps: int = 10
    eval_steps:    int = 100       # ประเมิน val ทุก 100 step
    save_steps:    int = 100
    save_total_limit: int = 3
    load_best_model_at_end: bool = True
    gradient_checkpointing: bool  = True
    dataloader_num_workers: int   = 4

    # ── Thinking mode ──────────────────────────────────────────────────────
    # ถ้าเป็น True จะใส่ <think></think> ว่างเปล่าในทุก response
    # เพื่อปิด chain-of-thought (เร็วกว่า สำหรับ task summarisation)
    disable_thinking: bool = True


# ═════════════════════════════════════════════════════════════════════════════
# 2. TOKENIZER & MODEL
# ═════════════════════════════════════════════════════════════════════════════
def load_model_and_tokenizer(args: ScriptArgs):
    print(f"⏳  Loading tokenizer: {args.model_name}")
    tokenizer = AutoTokenizer.from_pretrained(
        args.model_name,
        trust_remote_code=True,
        padding_side="right",      # สำหรับ causal LM
    )
    # Qwen3 มี pad token แล้ว ถ้าไม่มีให้เพิ่ม
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print(f"⏳  Loading model: {args.model_name}")
    model = AutoModelForCausalLM.from_pretrained(
        args.model_name,
        torch_dtype=torch.bfloat16 if args.bf16 else torch.float16,
        device_map="auto",                       # กระจายไปทุก GPU อัตโนมัติ
        trust_remote_code=True,
        attn_implementation="flash_attention_2", # ต้องการ flash-attn 2
    )

    if args.gradient_checkpointing:
        model.gradient_checkpointing_enable(
            gradient_checkpointing_kwargs={"use_reentrant": False}
        )

    return tokenizer, model


# ═════════════════════════════════════════════════════════════════════════════
# 3. PEFT / LoRA
# ═════════════════════════════════════════════════════════════════════════════
def apply_lora(model, args: ScriptArgs):
    target_modules = [m.strip() for m in args.lora_target.split(",")]
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        target_modules=target_modules,
        bias="none",
        inference_mode=False,
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()
    return model


# ═════════════════════════════════════════════════════════════════════════════
# 4. DATASET  –  ChatML → token ids (mask prompt tokens)
# ═════════════════════════════════════════════════════════════════════════════
def make_dataset(args: ScriptArgs, tokenizer):
    """โหลด jsonl แล้ว apply_chat_template และ mask prompt tokens"""

    raw = load_dataset(
        "json",
        data_files={"train": args.train_file, "validation": args.val_file},
    )

    def format_and_tokenize(example):
        messages = example["messages"]

        # ถ้าปิด thinking mode → เพิ่ม <think></think> ต่อท้าย user turn
        # (Qwen3 ใช้ enable_thinking flag ใน chat template)
        tokenized = tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=False,
            return_tensors=None,
            enable_thinking=not args.disable_thinking,  # Qwen3 flag
        )

        input_ids = tokenized if isinstance(tokenized, list) else tokenized["input_ids"]

        # ── mask prompt tokens (loss เฉพาะ assistant turn) ──────────────
        labels = list(input_ids)
        # หา position ของ <|im_start|>assistant
        im_start_id   = tokenizer.convert_tokens_to_ids("<|im_start|>")
        assistant_ids = tokenizer.encode("assistant", add_special_tokens=False)
        im_end_id     = tokenizer.convert_tokens_to_ids("<|im_end|>")

        in_assistant = False
        for i, tid in enumerate(input_ids):
            if not in_assistant:
                labels[i] = -100   # mask tokens ก่อน assistant
                # ตรวจว่าเจอ <|im_start|>assistant แล้ว
                if (tid == im_start_id and
                        i + len(assistant_ids) < len(input_ids) and
                        input_ids[i+1:i+1+len(assistant_ids)] == assistant_ids):
                    # mask หัว tag ด้วย
                    for j in range(i, i + 1 + len(assistant_ids)):
                        labels[j] = -100
                    in_assistant = True
            else:
                # พอเจอ <|im_end|> → ปิด assistant turn
                if tid == im_end_id:
                    in_assistant = False

        # ตัดให้ไม่เกิน max_seq_length
        input_ids = input_ids[:args.max_seq_length]
        labels    = labels[:args.max_seq_length]

        return {
            "input_ids":      input_ids,
            "attention_mask": [1] * len(input_ids),
            "labels":         labels,
        }

    tokenized_ds = raw.map(
        format_and_tokenize,
        remove_columns=raw["train"].column_names,
        num_proc=4,
        desc="Tokenising",
    )
    return tokenized_ds


# ═════════════════════════════════════════════════════════════════════════════
# 5. TRAINING
# ═════════════════════════════════════════════════════════════════════════════
def train(args: ScriptArgs):
    set_seed(args.seed)

    tokenizer, model = load_model_and_tokenizer(args)
    model = apply_lora(model, args)

    dataset = make_dataset(args, tokenizer)

    training_args = TrainingArguments(
        output_dir=args.output_dir,
        num_train_epochs=args.num_train_epochs,

        per_device_train_batch_size=args.per_device_train_batch_size,
        per_device_eval_batch_size=args.per_device_eval_batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,

        learning_rate=args.learning_rate,
        lr_scheduler_type=args.lr_scheduler_type,
        warmup_ratio=args.warmup_ratio,
        weight_decay=args.weight_decay,
        max_grad_norm=args.max_grad_norm,

        bf16=args.bf16,
        fp16=args.fp16,

        logging_dir=os.path.join(args.output_dir, "logs"),
        logging_steps=args.logging_steps,

        evaluation_strategy="steps",
        eval_steps=args.eval_steps,
        save_strategy="steps",
        save_steps=args.save_steps,
        save_total_limit=args.save_total_limit,
        load_best_model_at_end=args.load_best_model_at_end,
        metric_for_best_model="eval_loss",
        greater_is_better=False,

        dataloader_num_workers=args.dataloader_num_workers,
        remove_unused_columns=False,
        report_to="none",          # เปลี่ยนเป็น "wandb" ถ้าต้องการ log
        seed=args.seed,

        # ── Multi-GPU ──────────────────────────────────────────────────────
        ddp_find_unused_parameters=False,  # ลด overhead
    )

    data_collator = DataCollatorForSeq2Seq(
        tokenizer=tokenizer,
        model=model,
        padding=True,
        pad_to_multiple_of=8,
        label_pad_token_id=-100,
    )

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=dataset["train"],
        eval_dataset=dataset["validation"],
        tokenizer=tokenizer,
        data_collator=data_collator,
        max_seq_length=args.max_seq_length,
        dataset_text_field=None,   # เราใช้ input_ids โดยตรงแล้ว
        peft_config=None,          # apply LoRA ไปแล้วข้างต้น
    )

    print("\n🚀  Start training...")
    trainer.train()

    # ── บันทึก ──────────────────────────────────────────────────────────────
    final_dir = os.path.join(args.output_dir, "final")
    trainer.save_model(final_dir)
    tokenizer.save_pretrained(final_dir)
    print(f"\n✅  Saved LoRA adapter + tokenizer →  {final_dir}")


# ═════════════════════════════════════════════════════════════════════════════
# 6. ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    args = ScriptArgs()

    # ── รับ arg จาก command line (ถ้าต้องการ override) ────────────────────
    parser = argparse.ArgumentParser()
    for f in args.__dataclass_fields__:
        default = getattr(args, f)
        parser.add_argument(f"--{f}", type=type(default), default=default)
    cli = parser.parse_args()
    for f in args.__dataclass_fields__:
        setattr(args, f, getattr(cli, f))

    train(args)
