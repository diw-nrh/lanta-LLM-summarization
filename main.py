from graph import app
from nodes.document_store import document_store
from nodes.embedder import embedder
import os

def initialize_system():
    print("--- SYSTEM INITIALIZATION ---")
    json_path = os.path.join("data", "train_set.json")
    
    # 1. โหลดข้อมูล Text จาก JSON เข้าสู่ Store
    document_store.load_from_json(json_path)
    
    # 2. แปลง Text เป็น Vectors (Embeddings) สำหรับโหมด Vector Search (Round 1 & 2)
    print("Generating embeddings... This might take a while on the first run.")
    doc_count = len(document_store.texts)
    
    for i, (doc_id, paragraphs) in enumerate(document_store.texts.items(), 1):
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
            vectors = embedder.encode(chunked_texts)
            embeddings_dict = {para_ids[j]: vectors[j] for j in range(len(para_ids))}
            # เก็บเข้า Document Store ภายใต้ doc_id นั้นๆ (key ยังเป็น para_id เดิม)
            document_store.add_embeddings(doc_id, embeddings_dict)
            
        if i % 10 == 0 or i == doc_count:
            print(f"Processed {i}/{doc_count} documents for embeddings.")
            
    print("--- INITIALIZATION COMPLETE ---")

def main():
    initialize_system()
    
    print("\n--- STARTING LANGGRAPH PIPELINE ---")
    
    # จำลอง Input แบบ Single-Document (มี `doc_id` ชัดเจน)
    inputs = {
        "query_id": "Q0008",
        "doc_id": "doc_001", 
        "query": "การประชุมครั้งนี้ได้ข้อสรุปว่าอย่างไร",
        "retry_count": 0
    }
    
    print(f"Processing Query: {inputs['query_id']} for Document: {inputs['doc_id']}")
    
    # รัน Graph
    for output in app.stream(inputs):
        for node_name, node_state in output.items():
            print(f"--- FINISHED NODE: {node_name} ---")
            
    print("--- PIPELINE COMPLETE ---")

if __name__ == "__main__":
    main()
