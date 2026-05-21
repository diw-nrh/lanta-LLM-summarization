import numpy as np
from typing import List

class EmbeddingEngine:
    def __init__(self):
        # โหลด HuggingFace Embedding Models (e5, bge-m3, wangchanberta)
        pass
        
    def encode(self, texts: List[str], model_name: str) -> np.ndarray:
        """แปลง text เป็น vector ด้วย model ที่กำหนด"""
        pass
        
    def encode_query(self, query: str) -> dict:
        """แปลง query เป็น vector จากทุก models สำหรับเอาไป ensemble"""
        pass
