import argparse
import json
from graph import app
from nodes.document_store import document_store
from nodes.embedder import embedder
import os

def initialize_system(json_path, target_doc_id="doc_002"):
    print("--- SYSTEM INITIALIZATION ---")
    
    # 1. โหลดข้อมูล Text จาก JSON เข้าสู่ Store
    document_store.load_from_json(json_path)
    
    # กรองเฉพาะ doc ที่เราต้องการเทส
    target_docs = {k: v for k, v in document_store.texts.items() if k == target_doc_id}
    
    # 2. แปลง Text เป็น Vectors (Embeddings) สำหรับโหมด Vector Search (Round 1 & 2)
    print(f"Generating embeddings ONLY for {target_doc_id}... This will be fast!")
    doc_count = len(target_docs)
    
    for i, (doc_id, paragraphs) in enumerate(target_docs.items(), 1):
        para_ids = list(paragraphs.keys())
        texts = list(paragraphs.values())
        
        if texts:
            # Idea 3: Sliding Window Chunking (N-1, N, N+1)
            chunked_texts = []
            num_paras = len(texts)
            for j in range(num_paras):
                # ใช้ str() คลุมป้องกันบั๊กเผื่อข้อมูลใน JSON เป็นตัวเลข (เช่น ลำดับวาระ)
                prev_text = str(texts[j-1]) + " " if j > 0 else ""
                curr_text = str(texts[j])
                next_text = " " + str(texts[j+1]) if j < num_paras - 1 else ""
                
                # รวมเพื่อนบ้านซ้ายขวา
                window_text = prev_text + curr_text + next_text
                chunked_texts.append(window_text.strip())
                
            # แปลงเวกเตอร์จากก้อน Window
            try:
                vectors = embedder.encode(chunked_texts)
                embeddings_dict = {para_ids[j]: vectors[j] for j in range(len(para_ids))}
                # เก็บเข้า Document Store ภายใต้ doc_id นั้นๆ (key ยังเป็น para_id เดิม)
                document_store.add_embeddings(doc_id, embeddings_dict)
            except Exception as e:
                print(f"[WARN] Failed to encode embeddings for {doc_id}: {e}")
            
        if i % 10 == 0 or i == doc_count:
            print(f"Processed {i}/{doc_count} documents for embeddings.")
            
    print("--- INITIALIZATION COMPLETE ---")

def main():
    parser = argparse.ArgumentParser(description="LANTA LLM Summarization")
    parser.add_argument("--data", type=str, default=os.path.join("data", "train_set.json"), help="Path to JSON dataset")
    parser.add_argument("--limit", type=int, default=5, help="Number of queries to process (for testing)")
    args = parser.parse_args()
    
    json_path = args.data
    if not os.path.exists(json_path):
        print(f"[ERROR] Dataset not found: {json_path}")
        return

    first_doc_id = "doc_002"
    initialize_system(json_path, target_doc_id=first_doc_id)
    
    print("\n--- STARTING LANGGRAPH PIPELINE ---")
    
    with open(json_path, 'r', encoding='utf-8') as f:
        dataset = json.load(f)
        
    # ดึงเฉพาะคำถามที่เป็นของ doc_001 (Doc แรกสุด)
    first_doc_id = "doc_002"
    queries = [q for q in dataset.get("queries", []) if q.get("doc_id") == first_doc_id]
    
    if args.limit > 0:
        queries = queries[:args.limit]
    print(f"Found {len(queries)} queries for document {first_doc_id} to process (limited for testing).")
    
    for idx, q in enumerate(queries, 1):
        inputs = {
            "query_id": q["ID"],
            "doc_id": q["doc_id"], 
            "query": q["query"],
            "retry_count": 0
        }
        
        expected_refs = q.get("refs", [])
        expected_abstractive = q.get("abstractive", "")
        
        print(f"\n=======================================================")
        print(f"[{idx}/{len(queries)}] Processing Query: {inputs['query_id']} for Document: {inputs['doc_id']}")
        print(f"[QUERY]: {inputs['query']}")
        print(f"[EXPECTED REFS]: {expected_refs}")
        print(f"=======================================================")
        
        # รัน Graph
        try:
            final_state = None
            for output in app.stream(inputs):
                for node_name, node_state in output.items():
                    print(f"--- FINISHED NODE: {node_name} ---")
                    final_state = node_state
            
            # Print Comparison after completion
            if final_state:
                print(f"\n✅ --- RESULT COMPARISON FOR {inputs['query_id']} ---")
                print(f"   [EXPECTED REFS]: {expected_refs}")
                print(f"   [ACTUAL REFS]  : {final_state.get('refs', [])}")
                print(f"   ----------------------------------------")
                print(f"   [EXPECTED ABSTRACTIVE]:\n   {expected_abstractive}")
                print(f"   ----------------------------------------")
                print(f"   [ACTUAL ABSTRACTIVE]:\n   {final_state.get('abstractive', '')}")
                print(f"=======================================================\n")
                
        except Exception as e:
            print(f"[ERROR] Pipeline failed on {inputs['query_id']}: {e}")
            
    print("--- PIPELINE COMPLETE ---")

if __name__ == "__main__":
    main()
