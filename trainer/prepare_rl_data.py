import sys
import os
import asyncio
import json

# Add parent directory to path to import from main / nodes
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)
# Support LANTA directory structure where nodes is inside 'run/'
sys.path.append(os.path.join(parent_dir, "run"))

from nodes.agent_a_retriever import retriever_node
from nodes.document_store import document_store
from nodes.embedder import embedder
import nest_asyncio

nest_asyncio.apply()

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
                prev_text = str(texts[j-1]) + " " if j > 0 else ""
                curr_text = str(texts[j])
                next_text = " " + str(texts[j+1]) if j < num_paras - 1 else ""
                
                window_text = prev_text + curr_text + next_text
                chunked_texts.append(window_text.strip())
                
            try:
                vectors = embedder.encode(chunked_texts)
                embeddings_dict = {para_ids[j]: vectors[j] for j in range(len(para_ids))}
                document_store.add_embeddings(doc_id, embeddings_dict)
            except Exception as e:
                print(f"[WARN] Failed to encode embeddings for {doc_id}: {e}")
        
        if i % 10 == 0 or i == doc_count:
            print(f"Processed {i}/{doc_count} documents for embeddings.")
            
    print("--- INITIALIZATION COMPLETE ---")

async def process_query(q, semaphore):
    async with semaphore:
        query_id = q.get("query_id", q.get("ID", "UNKNOWN"))
        state = {
            "query": q["query"],
            "doc_id": q["doc_id"]
        }
        
        try:
            result = await retriever_node(state)
            retrieved_refs = result.get("refs", [])
            retrieved_context = result.get("context", "")
        except Exception as e:
            print(f"[ERROR] Agent A failed on {query_id}: {e}")
            retrieved_refs = []
            retrieved_context = "ไม่พบข้อมูล"
        
        return {
            "query_id": query_id,
            "doc_id": q["doc_id"],
            "query": q["query"],
            "retrieved_refs": retrieved_refs,
            "retrieved_context": retrieved_context,
            "sol_refs": q["refs"],
            "sol_abstractive": q["abstractive"]
        }

async def run(json_path):
    print(f"--- Loading data from {json_path} ---")
    
    # 1. Initialize DB and Embedder using main.py's function
    initialize_system(json_path)
    
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    queries = data.get("queries", [])
    print(f"\n[INFO] Start retrieving context for {len(queries)} queries...")
    
    semaphore = asyncio.Semaphore(15)
    tasks = []
    
    for q in queries:
        tasks.append(process_query(q, semaphore))
        
    results = await asyncio.gather(*tasks)
    
    output_data = {
        "docs": data.get("docs", []), # keep original docs just in case
        "queries": results
    }
    
    output_path = os.path.join(parent_dir, "data", "train_rl_rag.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
        
    print(f"\n✅ Successfully saved Agent A RAG results to: {output_path}")

if __name__ == "__main__":
    # Default to train_set.json
    train_json_path = os.path.join(parent_dir, "data", "train_set.json")
    if not os.path.exists(train_json_path):
        print(f"[ERROR] Could not find {train_json_path}")
        sys.exit(1)
        
    asyncio.run(run(train_json_path))
