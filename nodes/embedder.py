import numpy as np
from typing import List, Dict
import torch
try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

class EmbeddingEngine:
    def __init__(self):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.models = {}
        
        if SentenceTransformer:
            print(f"Loading embedding models on {self.device}...")
            # โหลดโมเดล Sentence-Transformers มาตรฐาน (โหลดแค่ BGE เป็นตัวหลักเพื่อประหยัด VRAM)
            self.models["bge-m3"] = SentenceTransformer("BAAI/bge-m3", device=self.device)
        else:
            print("[WARN] sentence-transformers not installed. Embedder will not work.")
        
    def encode(self, texts: List[str], model_name: str = "bge-m3") -> np.ndarray:
        """แปลง text เป็น vector ด้วย model ที่กำหนด"""
        model = self.models.get(model_name)
        if not model:
            raise ValueError(f"Model {model_name} not loaded.")
        
        # normalize_embeddings=True สำหรับการหา Cosine Similarity ด้วย Dot Product ได้เลย
        embeddings = model.encode(texts, normalize_embeddings=True)
        return np.array(embeddings)
        
    def encode_query(self, query: str) -> Dict[str, np.ndarray]:
        """แปลง query เป็น vector จากทุก models สำหรับเอาไป ensemble"""
        query_vectors = {}
        for name, model in self.models.items():
            vec = model.encode([query], normalize_embeddings=True)[0]
            query_vectors[name] = np.array(vec)
        return query_vectors

# Global instance
embedder = EmbeddingEngine()
