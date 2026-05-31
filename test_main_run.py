import argparse
import json
import os
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
#PROGRESS_LIB = "/benchmark_lib/progress"
RESULT_CSV_PATH = "/result/submission.csv"


#def call_progress(i):
#    """ฟังก์ชันเรียกใช้ progress ตามกติกาการแข่งขัน [cite: 431, 544]"""
#    if os.path.exists(PROGRESS_LIB):
#        os.system(f"{PROGRESS_LIB} {i}")
#    else:
#        print(
#            f"[DEBUG] ไม่พบ {PROGRESS_LIB} (ข้ามการเรียก progress สำหรับรันเทสบน Local)"
#        )


def initialize_system(json_path):
    print("--- SYSTEM INITIALIZATION ---")
    document_store.load_from_json(json_path)

    print(
        f"Generating embeddings for all documents... This might take a while on the first run."
    )
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

        if i % 10 == 0 or i == doc_count:
            print(f"Processed {i}/{doc_count} documents for embeddings.")

    print("--- INITIALIZATION COMPLETE ---")


async def process_single_query(inputs: dict, semaphore: asyncio.Semaphore):
    """รัน 1 คำถาม ภายใต้การควบคุมของ Semaphore เพื่อป้องกัน VRAM เต็ม"""
    async with semaphore:
        print(f"🚀 เริ่มประมวลผล Query: {inputs['query_id']} (Doc: {inputs['doc_id']})")
        try:
            final_state = await app.ainvoke(inputs)

            print(f"✅ --- RESULT FOR {inputs['query_id']} ---")
            print(f"   [ACTUAL REFS]  : {final_state.get('refs', [])}")
            print(f"   [ABSTRACTIVE]  : {final_state.get('abstractive', '')[:50]}...")
            print(f"------------------------------------------\n")

            return {
                "ID": inputs["query_id"],
                "abstractive": final_state.get("abstractive", ""),
                "refs": ",".join(final_state.get("refs", [])) if final_state.get("refs") else ""
            }
        except Exception as e:
            print(f"[ERROR] Pipeline failed on {inputs['query_id']}: {e}")
            return {"ID": inputs["query_id"], "abstractive": "Error", "refs": ""}


async def run_pipeline(json_path: str, concurrent_limit: int):
    initialize_system(json_path)

    with open(json_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    queries = dataset.get("queries", [])
    print(
        f"\nพบ {len(queries)} คำถาม | ⚡ กำลังรันทีละ {concurrent_limit} ข้อพร้อมกัน..."
    )

    semaphore = asyncio.Semaphore(concurrent_limit)

    tasks = []
    for q in queries:
        inputs = {
            "query_id": q["ID"],
            "doc_id": q["doc_id"],
            "query": q["query"],
            "retry_count": 0,
        }
        tasks.append(process_single_query(inputs, semaphore))

    results = await asyncio.gather(*tasks)

    # สร้าง Directory ปลายทางไว้ก่อนเผื่อกรณีรันเทสบน Local
    os.makedirs(os.path.dirname(RESULT_CSV_PATH), exist_ok=True)

    # บันทึกเป็น CSV (คอลัมน์ ID, abstractive, refs ตามกติกาเป๊ะๆ)
    df = pd.DataFrame(results)
    
    # บังคับลำดับคอลัมน์ (ID -> abstractive -> refs)
    df = df[["ID", "abstractive", "refs"]]
    
    # 🚨 เปลี่ยน encoding จาก utf-8-sig เป็น utf-8 เฉยๆ เพื่อป้องกันบั๊ก BOM (\xef\xbb\xbf)
    # 🚨 เพิ่ม lineterminator='\n' เพื่อป้องกันบั๊ก \r แอบติดไปกับคอลัมน์สุดท้าย (refs) เวลารันบน Windows
    df.to_csv(RESULT_CSV_PATH, index=False, encoding="utf-8", lineterminator='\n')
    print(f"\n--- 📝 บันทึกผลลัพธ์ลง {RESULT_CSV_PATH} สำเร็จ ({len(df)} รายการ) ---")

    # เรียกใช้ /benchmark_lib/progress หลังจากจบกระบวนการเขียนไฟล์ทั้งหมด [cite: 431, 544]
#    call_progress(len(queries))


def main():
    # หน่วงเวลา 10 วินาที เพื่อให้ระบบ Grafana+Loki ของผู้จัดดึง Log ได้ทัน
    print("⏳ หน่วงเวลา 10 วินาที เพื่อให้ระบบเก็บ Log (ตามคำแนะนำทีมงาน)...")
    time.sleep(10)

    # ค่า Default ชี้ไปที่ Absolute Path ของไฟล์ทดสอบ
    default_data_path = "/model/test/test.json"

    parser = argparse.ArgumentParser(description="LANTA LLM Summarization")
    parser.add_argument(
        "--data", type=str, default=default_data_path, help="Path to JSON dataset"
    )
    parser.add_argument(
        "--batch", type=int, default=10, help="จำนวน N ที่ต้องการรันพร้อมกัน"
    )
    args = parser.parse_args()

    if not os.path.exists(args.data):
        print(f"[ERROR] Dataset not found: {args.data}")
        sys.exit(1)  # โยน Exception เป็น 1 (ห้ามใช้ 0) หากเกิดข้อผิดพลาด

    # ครอบ try/except ตัวใหญ่สุดเพื่อจัดการ Error ที่อาจทำให้โปรแกรมหลุดแบบ exit 0
    try:
        asyncio.run(run_pipeline(args.data, args.batch))
    except Exception as e:
        print(f"[FATAL ERROR] เกิดข้อผิดพลาดร้ายแรงระหว่างรัน Pipeline: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
