"""
prepare_data.py
---------------
แปลง train_set.json → train.jsonl / val.jsonl
ในรูปแบบ instruction-following สำหรับ Qwen3 fine-tuning

โครงสร้าง input ที่ใช้:
  query: <คำถาม>
  context:
  P4: <text>
  P5: <text>
  ...

output (abstractive summary)
"""

import json
import random
from pathlib import Path


# ── Config ────────────────────────────────────────────────────────────────────
INPUT_JSON  = "train_set.json"      # path ไฟล์ต้นฉบับ
OUTPUT_DIR  = Path("data")
VAL_RATIO   = 0.05                  # 5% validation split
SEED        = 42
MAX_CTX_CHARS = 14_000              # ตัดบริบทที่ยาวมากเกินไป (ป้องกัน OOM)
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "คุณคือผู้ช่วยที่เชี่ยวชาญในการสรุปเนื้อหาจากเอกสาร "
    "ตอบเป็นภาษาไทยเท่านั้น ตอบกระชับและครบถ้วนตามที่ถามโดยไม่เพิ่มข้อมูลที่ไม่มีในบริบท"
)


def build_user_message(query: str, refs: list[str], para_map: dict[str, str]) -> str:
    """สร้าง user message จาก query + paragraph references"""
    context_lines = []
    for pid in refs:
        text = para_map.get(pid, "").strip()
        if text:
            context_lines.append(f"{pid}: {text}")

    context_str = "\n".join(context_lines)

    # ตัดถ้ายาวเกิน
    if len(context_str) > MAX_CTX_CHARS:
        context_str = context_str[:MAX_CTX_CHARS] + "\n... [ตัดบริบทที่เหลือออก]"

    return (
        f"คำถาม: {query}\n\n"
        f"บริบทจากเอกสาร:\n{context_str}"
    )


def load_and_convert(input_path: str) -> list[dict]:
    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    # สร้าง dict  doc_id → { para_id → text }
    doc_map: dict[str, dict[str, str]] = {}
    for doc in data["docs"]:
        doc_map[doc["doc_id"]] = {
            p["para_id"]: p["text"]
            for p in doc["paragraphs"]
        }

    samples = []
    for q in data["queries"]:
        doc_id   = q["doc_id"]
        query    = q["query"]
        refs     = q["refs"]
        answer   = q["abstractive"]

        para_map = doc_map.get(doc_id, {})
        user_msg = build_user_message(query, refs, para_map)

        # Qwen3 chat format (ChatML)
        sample = {
            "messages": [
                {"role": "system",    "content": SYSTEM_PROMPT},
                {"role": "user",      "content": user_msg},
                {"role": "assistant", "content": answer},
            ]
        }
        samples.append(sample)

    return samples


def split_and_save(samples: list[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    random.seed(SEED)
    random.shuffle(samples)

    val_n   = max(1, int(len(samples) * VAL_RATIO))
    val     = samples[:val_n]
    train   = samples[val_n:]

    def write_jsonl(path: Path, items: list[dict]) -> None:
        with open(path, "w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    write_jsonl(output_dir / "train.jsonl", train)
    write_jsonl(output_dir / "val.jsonl",   val)

    print(f"✅  train: {len(train):,} samples  →  {output_dir/'train.jsonl'}")
    print(f"✅  val  : {len(val):,}   samples  →  {output_dir/'val.jsonl'}")


if __name__ == "__main__":
    samples = load_and_convert(INPUT_JSON)
    split_and_save(samples, OUTPUT_DIR)

    # แสดงตัวอย่าง
    print("\n── ตัวอย่าง sample แรก ──")
    ex = samples[0]
    for msg in ex["messages"]:
        role    = msg["role"].upper()
        content = msg["content"][:200] + ("..." if len(msg["content"]) > 200 else "")
        print(f"[{role}]\n{content}\n")
