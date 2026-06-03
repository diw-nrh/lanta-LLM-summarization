"""
inference.py
────────────
ทดสอบโมเดลที่เทรนแล้ว (LoRA adapter + base Qwen3-8B)

ใช้:
    python inference.py --adapter_path outputs/qwen3-8b-lora-summary/final
"""

import argparse
import json
import torch
from pathlib import Path
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel


SYSTEM_PROMPT = (
    "คุณคือผู้ช่วยที่เชี่ยวชาญในการสรุปเนื้อหาจากเอกสาร "
    "ตอบเป็นภาษาไทยเท่านั้น ตอบกระชับและครบถ้วนตามที่ถามโดยไม่เพิ่มข้อมูลที่ไม่มีในบริบท"
)

BASE_MODEL = "Qwen/Qwen3-8B"


def load_model(adapter_path: str):
    print(f"⏳  Loading base model: {BASE_MODEL}")
    tokenizer = AutoTokenizer.from_pretrained(adapter_path, trust_remote_code=True)

    base = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
        attn_implementation="flash_attention_2",
    )

    print(f"⏳  Loading LoRA adapter: {adapter_path}")
    model = PeftModel.from_pretrained(base, adapter_path)
    model.eval()
    return tokenizer, model


def summarise(
    tokenizer,
    model,
    query: str,
    refs_text: str,
    max_new_tokens: int = 512,
    enable_thinking: bool = False,   # False = ปิด CoT (เร็วกว่า)
) -> str:
    """
    Parameters
    ----------
    query       : คำถาม
    refs_text   : บริบทที่ต่อกันแล้ว เช่น "P4: ข้อความ\nP5: ข้อความ"
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": f"คำถาม: {query}\n\nบริบทจากเอกสาร:\n{refs_text}"},
    ]

    input_ids = tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,
        tokenize=True,
        return_tensors="pt",
        enable_thinking=enable_thinking,
    ).to(model.device)

    with torch.no_grad():
        output_ids = model.generate(
            input_ids,
            max_new_tokens=max_new_tokens,
            do_sample=False,        # deterministic (greedy)
            temperature=None,
            top_p=None,
            repetition_penalty=1.1,
            pad_token_id=tokenizer.eos_token_id,
        )

    # ตัดเฉพาะ tokens ที่ generate ใหม่
    new_tokens = output_ids[0][input_ids.shape[-1]:]
    response   = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

    # ถ้าเปิด thinking mode → ตัด <think>...</think> ออก
    if "<think>" in response and "</think>" in response:
        response = response.split("</think>")[-1].strip()

    return response


# ── Demo ──────────────────────────────────────────────────────────────────────
def demo(adapter_path: str):
    tokenizer, model = load_model(adapter_path)

    # ตัวอย่างจาก train_set.json
    query = "ในระเบียบวาระที่ 4 เรื่องพิจารณากลั่นกรองเรื่องที่ประชาชนเสนอให้คณะกรรมาธิการศึกษาแก้ไขปัญหา คณะกรรมาธิการได้ให้ข้อเสนอแนะอย่างไร"
    refs_text = (
        "P55: ระเบียบวาระที่ ๔ เรื่องพิจารณา\n"
        "P56: - พิจารณากลั่นกรองเรื่องที่ประชาชนเสนอให้คณะกรรมาธิการศึกษาแก้ไขปัญหา\n"
        "P57: นายประยุทธ์ ศิริพานิชย์ ที่ปรึกษาคณะกรรมาธิการ ได้ให้ข้อเสนอแนะว่า "
             "การดำเนินการศึกษาเรื่องร้องเรียนอาจไม่เข้าข่ายการสอบหาข้อเท็จจริง\n"
        "P58: ด้วยบทบัญญัติแห่งรัฐธรรมนูญแห่งราชอาณาจักรไทย มาตรา ๑๒๙ วรรคสาม..."
    )

    print("=" * 60)
    print(f"Query: {query}\n")
    print("Context:", refs_text[:200], "...\n")
    print("=" * 60)

    result = summarise(tokenizer, model, query, refs_text)
    print(f"\n📝  Summary:\n{result}")


# ── Batch evaluation ──────────────────────────────────────────────────────────
def batch_eval(adapter_path: str, data_file: str, output_file: str, n: int = 50):
    """ประเมินบน n ตัวอย่างแรกจาก val.jsonl และบันทึกผล"""
    tokenizer, model = load_model(adapter_path)

    results = []
    with open(data_file) as f:
        for i, line in enumerate(f):
            if i >= n:
                break
            sample = json.loads(line)
            messages = sample["messages"]
            query_content = messages[1]["content"]  # user turn
            expected      = messages[2]["content"]  # assistant turn

            # ดึง refs_text จาก user message (หลัง "บริบทจากเอกสาร:\n")
            parts = query_content.split("บริบทจากเอกสาร:\n", 1)
            query     = parts[0].replace("คำถาม: ", "").strip()
            refs_text = parts[1] if len(parts) > 1 else ""

            predicted = summarise(tokenizer, model, query, refs_text)
            results.append({
                "query":    query,
                "expected": expected,
                "predicted": predicted,
            })
            print(f"[{i+1}/{n}] Done")

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n✅  Saved {len(results)} results → {output_file}")


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--adapter_path", type=str,
        default="outputs/qwen3-8b-lora-summary/final",
        help="path ของ LoRA adapter ที่บันทึกไว้"
    )
    parser.add_argument("--mode", choices=["demo", "eval"], default="demo")
    parser.add_argument("--val_file",    default="data/val.jsonl")
    parser.add_argument("--output_file", default="eval_results.json")
    parser.add_argument("--n",           type=int, default=50)
    args = parser.parse_args()

    if args.mode == "demo":
        demo(args.adapter_path)
    else:
        batch_eval(args.adapter_path, args.val_file, args.output_file, args.n)
