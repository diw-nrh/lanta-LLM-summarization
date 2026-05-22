import numpy as np
from typing import List, Dict
from openai import OpenAI

class EmbeddingEngine:
    def __init__(self):
        print("Initializing Novita API for Embeddings...")
        try:
            self.client = OpenAI(
                api_key="sk_r8iPD9QTonepwvFVEmqTaH4gL8PQVX6UGSGyGhh1-WI",
                base_url="https://api.novita.ai/openai"
            )
            self.models = ["bge-m3"]
            print("--- 🚀 Novita API Embedder Initialized! ---")
        except Exception as e:
            print(f"[ERROR] Failed to initialize Embedder API: {e}")
        
    def encode(self, texts: List[str], model_name: str = "bge-m3") -> np.ndarray:
        """แปลง text เป็น vector ด้วย model ที่กำหนดผ่าน Novita API (แบบ Batch)"""
        all_embeddings = []
        batch_size = 10  # แบ่งส่งทีละ 10 ย่อหน้า เพื่อป้องกัน 413 Payload Too Large
        
        try:
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i + batch_size]
                response = self.client.embeddings.create(
                    model="baai/bge-m3",
                    input=batch
                )
                embeddings = [item.embedding for item in response.data]
                all_embeddings.extend(embeddings)
                
            return np.array(all_embeddings)
        except Exception as e:
            raise RuntimeError(f"API Embedding failed: {e}")
        
    def encode_query(self, query: str) -> Dict[str, np.ndarray]:
        """แปลง query เป็น vector จากทุก models สำหรับเอาไป ensemble"""
        query_vectors = {}
        for name in self.models:
            try:
                response = self.client.embeddings.create(
                    model="baai/bge-m3",
                    input=[query]
                )
                vec = response.data[0].embedding
                query_vectors[name] = np.array(vec)
            except Exception as e:
                print(f"[ERROR] Failed to encode query via API: {e}")
                query_vectors[name] = np.array([])
                
        return query_vectors

# Global instance
embedder = EmbeddingEngine()
