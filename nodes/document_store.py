import numpy as np
from typing import Dict, List, Tuple

class DocumentStore:
    def __init__(self):
        # Format: {doc_id: {para_id: text}}
        self.texts: Dict[str, Dict[str, str]] = {}
        # Format: {doc_id: {para_id: embedding_vector}}
        self.embeddings: Dict[str, Dict[str, np.ndarray]] = {}

    def get_paragraphs(self, doc_id: str) -> Dict[str, str]:
        """ดึง text ทั้งหมดของ doc_id"""
        pass

    def get_embeddings(self, doc_id: str) -> Dict[str, np.ndarray]:
        """ดึง embeddings ทั้งหมดของ doc_id (cached)"""
        pass

    def get_text(self, doc_id: str, para_id: str) -> str:
        """ดึง text ของ paragraph ระบุ"""
        pass
