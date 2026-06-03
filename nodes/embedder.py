import numpy as np
import os
from typing import List, Dict
from sentence_transformers import SentenceTransformer

class EmbeddingEngine:
    def __init__(self):
        print("Initializing Local Embeddings...")
        try:
            # 1. เปลี่ยนจากการเชื่อมต่อ API เป็นการโหลดไฟล์ Local แทน
            current_dir = os.path.dirname(os.path.abspath(__file__))
            model_path = os.path.join(current_dir, "../../models/bge-m3")
            self.client = SentenceTransformer(model_path)
            
            self.models = ["bge-m3"]
            print("--- 🚀 Local Embedder Initialized! ---")
        except Exception as e:
            print(f"[ERROR] Failed to initialize Embedder: {e}")
        
    def encode(self, texts: List[str], model_name: str = "bge-m3") -> np.ndarray:
        """แปลง text เป็น vector ด้วย model ที่กำหนด (แบบ Batch)"""
        all_embeddings = []
        batch_size = 10  # แบ่งส่งทีละ 10 ย่อหน้า เพื่อป้องกัน 413 Payload Too Large
        
        try:
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i + batch_size]
                
                # 2. เปลี่ยนจุดที่เรียก API เป็นการสั่ง encode ผ่านโมเดล
                embeddings = self.client.encode(batch, show_progress_bar=False)
                
                all_embeddings.extend(embeddings)
                
            return np.array(all_embeddings)
        except Exception as e:
            raise RuntimeError(f"Embedding failed: {e}")
#          try:
#            # โยน text ทั้งหมดเข้าไปเลย แล้วปรับ batch_size ให้ใหญ่ขึ้น (A100 รับ 64-128 สบายๆ ครับ)
#            # SentenceTransformer จะจัดการหั่นและส่งเข้า GPU ให้เองอย่างรวดเร็ว
#            embeddings = self.client.encode(texts, batch_size=64, show_progress_bar=False)
#            
#            return np.array(embeddings)
#        except Exception as e:
#            raise RuntimeError(f"Embedding failed: {e}")
        
    def encode_query(self, query: str) -> Dict[str, np.ndarray]:
        """แปลง query เป็น vector จากทุก models สำหรับเอาไป ensemble"""
        query_vectors = {}
        for name in self.models:
            try:
                # 3. เปลี่ยนจุดที่เรียก API เป็นการสั่ง encode ผ่านโมเดล
                vec = self.client.encode([query], show_progress_bar=False)[0]
                
                query_vectors[name] = np.array(vec)
            except Exception as e:
                print(f"[ERROR] Failed to encode query: {e}")
                query_vectors[name] = np.array([])
                
        return query_vectors

# Global instance
embedder = EmbeddingEngine()

class RerankerEngine:
    def __init__(self):
        print("Initializing Reranker...")
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            current_dir = os.path.dirname(os.path.abspath(__file__))
            self.model_path = os.path.abspath(os.path.join(current_dir, "../../models/bge-reranker-v2-m3"))
            if os.path.exists(self.model_path):
                self.tokenizer = AutoTokenizer.from_pretrained(self.model_path)
                self.model = AutoModelForSequenceClassification.from_pretrained(self.model_path).half().cuda().eval()
                print("--- 🚀 Reranker Initialized! ---")
                self.ready = True
            else:
                print(f"[WARN] Reranker model path not found: {self.model_path}")
                self.ready = False
        except Exception as e:
            print(f"[WARN] Reranker not initialized: {e}")
            self.ready = False

    def compute_scores(self, query: str, texts: List[str]) -> List[float]:
        if not self.ready or not texts: return [0.0] * len(texts)
        pairs = [[query, t] for t in texts]
        try:
            import torch
            with torch.no_grad():
                inputs = self.tokenizer(pairs, padding=True, truncation=True, return_tensors='pt', max_length=1024).to('cuda')
                scores = self.model(**inputs, return_dict=True).logits.view(-1, ).float().cpu().numpy().tolist()
            return scores
        except Exception as e:
            print(f"[ERROR] Reranking failed: {e}")
            return [0.0] * len(texts)

reranker = RerankerEngine()