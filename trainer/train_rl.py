import os
import json
import argparse
from dataclasses import dataclass
import re

import pandas as pd
import torch
import torch.nn.functional as F
from datasets import Dataset
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import LoraConfig, get_peft_model, TaskType
from trl import GRPOTrainer, GRPOConfig
from accelerate import PartialState # เพิ่ม accelerate เพื่อจัดการ GPU 

from sentence_transformers import SentenceTransformer
from pythainlp.tokenize import word_tokenize
from rouge_score import rouge_scorer
from rouge_score.tokenizers import Tokenizer

# ═══════════════════════════════════════════════════════
# 1. EVALUATION UTILS & REWARD FUNCTIONS
# ═══════════════════════════════════════════════════════
def tokenize_thai(text):
    if not isinstance(text, str) or text.strip() == "":
        return ""
    tokens = word_tokenize(text, engine="newmm", keep_whitespace=False)
    return " ".join(tokens)

class ThaiSpaceTokenizer(Tokenizer):
    def tokenize(self, text):
        return text.split(" ")

def calculate_iou(list_pred, list_sol):
    set_pred = set(list_pred) if isinstance(list_pred, list) else set()
    set_sol = set(list_sol) if isinstance(list_sol, list) else set()
    if not set_sol: return 0.0
    if not set_pred: return 0.0
    return len(set_pred.intersection(set_sol)) / len(set_pred.union(set_sol))

# Load bge-m3 globally to avoid reloading inside the reward function
embedding_model = None

def get_embedding_model(model_path):
    global embedding_model
    if embedding_model is None:
        # กำหนดให้โหลดโมเดลลง GPU ที่ถูกต้องตาม DDP process แบบไดนามิก
        device_index = PartialState().local_process_index
        device = f"cuda:{device_index}" if torch.cuda.is_available() else "cpu"
        embedding_model = SentenceTransformer(model_path, device=device)
    return embedding_model

def competition_reward_func(completions, sol_refs, sol_abstractive, **kwargs):
    import json
    
    pred_abstractives = []
    pred_refs_list = []
    format_rewards = []
    
    for comp in completions:
        text = comp[0]["content"] if isinstance(comp, list) else str(comp)
        text = text.strip()
        
        try:
            match = re.search(r'\{.*\}', text, re.DOTALL)
            json_text = match.group(0) if match else text
                
            parsed = json.loads(json_text)
            
            a = parsed.get("abstractive", "")
            r_list = parsed.get("used_refs", [])
            
            if isinstance(r_list, str):
                r_list = [r.strip() for r in r_list.split(",") if r.strip()]
            
            pred_abstractives.append(str(a))
            pred_refs_list.append(r_list)
            format_rewards.append(0.2)
        except Exception:
            pred_abstractives.append("")
            pred_refs_list.append([])
            format_rewards.append(-0.5)

    iou_scores = [calculate_iou(p, s) for p, s in zip(pred_refs_list, sol_refs)]
    
    scorer = rouge_scorer.RougeScorer(['rougeL'], use_stemmer=False, tokenizer=ThaiSpaceTokenizer())
    sol_toks = [tokenize_thai(s) for s in sol_abstractive]
    pred_toks = [tokenize_thai(p) for p in pred_abstractives]
    
    rouge_scores = []
    for g, p in zip(sol_toks, pred_toks):
        if not p:
            rouge_scores.append(0.0)
        else:
            rouge_scores.append(scorer.score(g, p)['rougeL'].fmeasure)
            
    bge = get_embedding_model(kwargs.get('bge_model_path'))
    
    safe_pred_abstractives = [p if p else "ไม่มีข้อความ" for p in pred_abstractives]
    
    texts = sol_abstractive + safe_pred_abstractives
    embeddings = bge.encode(texts, batch_size=32, convert_to_tensor=True, normalize_embeddings=True)
    
    ref_emb = embeddings[0:len(sol_abstractive)]
    pred_emb = embeddings[len(sol_abstractive):]
    
    ss_scores = F.cosine_similarity(pred_emb, ref_emb, dim=1).cpu().numpy().tolist()
    
    final_rewards = []
    wss, wrl, wj = 0.45, 0.35, 0.2
    
    for ss, rl, iou, fmt in zip(ss_scores, rouge_scores, iou_scores, format_rewards):
        score = (wss * ss) + (wrl * rl) + (wj * iou)
        final_rewards.append(score + fmt)
        
    return final_rewards

# ═══════════════════════════════════════════════════════
# 2. CONFIG
# ═══════════════════════════════════════════════════════
@dataclass
class RLScriptArgs:
    model_name: str = "/lustrefs/disk/project/zz991000-zdeva/zz991012/my_workspace/submission/models/Qwen3-8B"
    bge_model_path: str = "/lustrefs/disk/project/zz991000-zdeva/zz991012/my_workspace/submission/models/bge-m3"
    train_file: str = "data/train_rl_rag.json"
    
    lora_r:       int   = 32
    lora_alpha:   int   = 64
    lora_dropout: float = 0.05
    lora_target:  str   = "q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj"

    output_dir:                  str   = "models_rl"
    num_train_epochs:            int   = 1 
    per_device_train_batch_size: int   = 1
    gradient_accumulation_steps: int   = 4
    learning_rate:               float = 1e-5 
    max_completion_length:       int   = 1024 
    num_generations:             int   = 2 

    bf16: bool = True  # เปลี่ยนเป็น True
    gradient_checkpointing: bool = True # เพิ่มเข้ามาเพื่อประหยัด VRAM
    seed: int  = 42

# ═══════════════════════════════════════════════════════
# 3. DATASET PREPARATION
# ═══════════════════════════════════════════════════════
SYSTEM_PROMPT = (
    "คุณคือผู้ช่วยที่เชี่ยวชาญในการสรุปเนื้อหาจากเอกสาร "
    "อ่านคำถามและบริบท จากนั้นให้คิดวิเคราะห์แล้วตอบในรูปแบบ JSON object ดังนี้:\n"
    "{\n"
    '  "reasoning": "วิเคราะห์ว่าต้องใช้พารากราฟไหน",\n'
    '  "used_refs": ["P1", "P2"],\n'
    '  "abstractive": "พิมพ์สรุปเนื้อหาตรงนี้"\n'
    "}\n"
    "ตอบกลับเป็น JSON format ที่ถูกต้องเท่านั้น"
)

def build_prompt(query: str, retrieved_context: str) -> str:
    return f"คำถาม: {query}\n\nบริบทจากเอกสาร:\n{retrieved_context}"

def load_rl_dataset(input_path: str):
    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    prompts, sol_refs_list, sol_abs_list = [], [], []
    
    for q in data.get("queries", []):
        query    = q.get("query", "")
        context  = q.get("retrieved_context", "")
        sol_refs = q.get("sol_refs", [])
        answer   = q.get("sol_abstractive", "")
        
        user_msg = build_prompt(query, context)
        
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg}
        ]
        
        prompts.append(messages)
        sol_refs_list.append(sol_refs)
        sol_abs_list.append(answer)

    ds = Dataset.from_dict({
        "prompt": prompts,
        "sol_refs": sol_refs_list,
        "sol_abstractive": sol_abs_list
    })
    return ds

# ═══════════════════════════════════════════════════════
# 4. TRAIN
# ═══════════════════════════════════════════════════════
def train(args: RLScriptArgs):
    get_embedding_model(args.bge_model_path)
    
    dataset = load_rl_dataset(args.train_file)
    
    target_modules = [m.strip() for m in args.lora_target.split(",")]
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        target_modules=target_modules,
        bias="none",
    )

    training_args = GRPOConfig(
        output_dir=args.output_dir,
        learning_rate=args.learning_rate,
        per_device_train_batch_size=args.per_device_train_batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        num_train_epochs=args.num_train_epochs,
        bf16=args.bf16,
        gradient_checkpointing=args.gradient_checkpointing, # เปิดใช้งาน
        ddp_find_unused_parameters=False,                   # ปิดเพื่อแก้ warning และเทรนเร็วขึ้น
        max_completion_length=args.max_completion_length,
        num_generations=args.num_generations,
        logging_steps=10,
        save_steps=20,
        seed=args.seed,
    )
    
    def wrapped_reward(prompts, completions, sol_refs, sol_abstractive, **kwargs):
        return competition_reward_func(completions, sol_refs, sol_abstractive, bge_model_path=args.bge_model_path)
    
    # ทางเลือกเพิ่มเติม: โหลด Base model ด้วย bfloat16 ตั้งแต่ต้นเพื่อให้แน
    # ทางเลือกเพิ่มเติม: โหลด Base model ด้วย bfloat16 ตั้งแต่ต้นเพื่อให้แน่ใจเรื่องการจัดการ Memory
    model = AutoModelForCausalLM.from_pretrained(
        args.model_name,
        torch_dtype=torch.bfloat16,
    )

    trainer = GRPOTrainer(
        model=model,
        reward_funcs=[wrapped_reward],
        args=training_args,
        train_dataset=dataset,
        peft_config=lora_config
    )

    if hasattr(trainer, "model") and hasattr(trainer.model, "generation_config"):
        trainer.model.generation_config.do_sample = True
        trainer.model.generation_config.temperature = 0.9
        trainer.model.generation_config.top_p = 0.9
        trainer.model.generation_config.max_new_tokens = args.max_completion_length

    print("\n🚀 Start RL Training (GRPO)...")
    trainer.train()

    final_dir = os.path.join(args.output_dir, "final")
    trainer.save_model(final_dir)
    print(f"\n✅ Saved RL Model → {final_dir}")

if __name__ == "__main__":
    args = RLScriptArgs()
    parser = argparse.ArgumentParser()
    
    # ดึงค่าจาก dataclass มาสร้างเป็น Argument สำหรับรับค่าจาก Command Line
    for f in args.__dataclass_fields__:
        default = getattr(args, f)
        # ตรวจสอบชนิดข้อมูลเพื่อให้รับค่าได้ถูกต้อง
        parser.add_argument(f"--{f}", type=type(default), default=default)
        
    cli, _ = parser.parse_known_args()
    
    # อัปเดตค่ากลับเข้าไปใน dataclass
    for f in args.__dataclass_fields__:
        setattr(args, f, getattr(cli, f))
        
    train(args)