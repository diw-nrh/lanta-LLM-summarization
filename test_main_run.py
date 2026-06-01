import argparse
import json
import os
# สั่ง vLLM ให้หยุดความล้ำสมัย ปิด V1 Engine แล้วกลับไปใช้ V0 เหมือนยุค 0.7.3
os.environ["VLLM_USE_V1"] = "0"
# บังคับวิธีสร้าง Process ให้ไม่เกิด Error แดง
os.environ["VLLM_WORKER_MULTIPROC_METHOD"] = "spawn"
import asyncio
import pandas as pd
import time
import sys
from graph import app
from nodes.document_store import document_store
from nodes.embedder import embedder
import nest_asyncio

nest_asyncio.apply()

# ระบุ Absolute Path ตามที่กติกาการแข่งขันกำหนด
PROGRESS_LIB = "/benchmark_lib/progress"
RESULT_CSV_PATH = "/lustrefs/disk/project/zz991000-zdeva/zz991012/my_workspace/submission/result/submission.csv"
default_data_path = "/lustrefs/disk/project/zz991000-zdeva/zz991012/my_workspace/submission/model/test/test.json"


def call_progress(i):
    """ฟังก์ชันเรียกใช้ progress ตามกติกาการแข่งขัน"""
    if os.path.exists(PROGRESS_LIB):
        os.system(f"{PROGRESS_LIB} {i}")


def initialize_system(json_path):
    print("--- SYSTEM INITIALIZATION ---")
    document_store.load_from_json(json_path)

    print(f"Generating embeddings for all documents... This might take a while on the first run.")
    doc_count = len(document_store.texts)

    for i, (doc_id, paragraphs) in enumerate(document_store.texts.items(), 1):
        para_ids = list(paragraphs.keys())
        texts = list(paragraphs.values())

        if texts:
            chunked_texts = []
            num_paras = len(texts)
            for j in range(num_paras):
                prev_text = str(texts[j - 1]) + " " if j > 0 else ""
                curr_text = str(texts[j])
                next_text = " " + str(texts[j + 1]) if j < num_paras - 1 else ""

                window_text = prev_text + curr_text + next_text
                chunked_texts.append(window_text.strip())

            try:
                vectors = embedder.encode(chunked_texts)
                embeddings_dict = {
                    para_ids[j]: vectors[j] for j in range(len(para_ids))
                }
                document_store.add_embeddings(doc_id, embeddings_dict)
            except Exception as e:
                print(f"[WARN] Failed to encode embeddings for {doc_id}: {e}")

        # 🌟 จุดที่ 1: ส่ง Heartbeat (0%) กระตุ้นระบบเป็นระยะช่วงที่ทำ Embedding
        if i % 10 == 0 or i == doc_count:
            print(f"Processed {i}/{doc_count} documents for embeddings.")
            call_progress(0) 

    print("--- INITIALIZATION COMPLETE ---")


async def process_single_query(inputs: dict, semaphore: asyncio.Semaphore):
    """รัน 1 คำถาม ภายใต้การควบคุมของ Semaphore"""
    async with semaphore:
        print(f"🚀 เริ่มประมวลผล Query: {inputs['query_id']} (Doc: {inputs['doc_id']})")
        try:
            final_state = await app.ainvoke(inputs)

            print(f"✅ --- RESULT FOR {inputs['query_id']} ---")
            print(f"   [QUERY] {inputs['query']}")
            print(f"   [ACTUAL REFS]  : {final_state.get('refs', [])}")
            print(f"   [REFS USED] {final_state.get('used_refs', [])}")
            print(f"   [ABSTRACTIVE]  : {final_state.get('abstractive', '')[:50]}...")
            print(f"------------------------------------------\n")

            return {
                "ID": inputs["query_id"],
                "abstractive": final_state.get("abstractive", ""),
                "refs": (
                    ",".join(final_state.get("used_refs", []))
                    if final_state.get("used_refs")
                    else ""
                ),
            }
        except Exception as e:
            print(f"[ERROR] Pipeline failed on {inputs['query_id']}: {e}")
            return {"ID": inputs["query_id"], "abstractive": "Error", "refs": ""}


# 🌟 จุดที่ 2: ฟังก์ชันสำหรับหุ้มงาน เพื่ออัปเดต Progress ทันทีที่แต่ละข้อทำเสร็จ
async def task_wrapper(inputs: dict, semaphore: asyncio.Semaphore, progress_state: dict):
    result = await process_single_query(inputs, semaphore)
    
    # นับจำนวนข้อที่เสร็จและเรียกหลอด Progress
    progress_state["count"] += 1
    call_progress(progress_state["count"])
    
    return result


async def run_pipeline(json_path: str, concurrent_limit: int):
    initialize_system(json_path)

    with open(json_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    queries = dataset.get("queries", [])
    print(f"\nพบ {len(queries)} คำถาม | ⚡ กำลังรันทีละ {concurrent_limit} ข้อพร้อมกัน...")

    semaphore = asyncio.Semaphore(concurrent_limit)
    progress_state = {"count": 0} # ตัวแปรส่วนกลางสำหรับนับยอด
    
    tasks = []
    for q in queries:
        inputs = {
            "query_id": q["ID"],
            "doc_id": q["doc_id"],
            "query": q["query"],
            "retry_count": 0,
        }
        # 🌟 เรียกใช้ Wrapper แทนการเรียกตรงๆ
        tasks.append(task_wrapper(inputs, semaphore, progress_state))

    results = await asyncio.gather(*tasks)

    os.makedirs(os.path.dirname(RESULT_CSV_PATH), exist_ok=True)
    df = pd.DataFrame(results)
    df.to_csv(RESULT_CSV_PATH, index=False, encoding="utf-8", lineterminator="\n")
    print(f"\n--- 📝 บันทึกผลลัพธ์ลง {RESULT_CSV_PATH} สำเร็จ ({len(df)} รายการ) ---")
    
    # เรียกครั้งสุดท้ายเพื่อความชัวร์
    call_progress(len(queries))


def main():
    print("⏳ หน่วงเวลา 10 วินาที เพื่อให้ระบบเก็บ Log (ตามคำแนะนำทีมงาน)...")
    time.sleep(10)
    
    # 🌟 จุดที่ 3: เรียก Progress ทันทีตั้งแต่เริ่ม เพื่อต่ออายุ Watchdog ไม่ให้มันตัดจบ!
    call_progress(0)

    parser = argparse.ArgumentParser(description="LANTA LLM Summarization")
    parser.add_argument(
        "--data", type=str, default=default_data_path, help="Path to JSON dataset"
    )
    parser.add_argument(
        "--batch", type=int, default=5, help="จำนวน N ที่ต้องการรันพร้อมกัน"
    )
    args = parser.parse_args()

    if not os.path.exists(args.data):
        print(f"[ERROR] Dataset not found: {args.data}")
        sys.exit(1)

    try:
        asyncio.run(run_pipeline(args.data, args.batch))
    except Exception as e:
        print(f"[FATAL ERROR] เกิดข้อผิดพลาดร้ายแรงระหว่างรัน Pipeline: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()