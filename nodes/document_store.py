import numpy as np
from typing import Dict
import json
import os

class DocumentStore:
    def __init__(self):
        # Format: {doc_id: {para_id: text}}
        self.texts: Dict[str, Dict[str, str]] = {}
        # Format: {doc_id: {para_id: embedding_vector}}
        self.embeddings: Dict[str, Dict[str, np.ndarray]] = {}

    def add_document(self, doc_id: str, paragraphs: Dict[str, str]):
        """เพิ่มเอกสารเข้าสู่ระบบ"""
        if doc_id not in self.texts:
            self.texts[doc_id] = {}
        self.texts[doc_id].update(paragraphs)

    def add_embeddings(self, doc_id: str, embeddings: Dict[str, np.ndarray]):
        """เพิ่ม Embeddings สำหรับเอกสาร"""
        if doc_id not in self.embeddings:
            self.embeddings[doc_id] = {}
        self.embeddings[doc_id].update(embeddings)

    def load_from_json(self, filepath: str):
        """อ่านไฟล์ train_set.json และโหลดข้อมูลเข้า Store อัตโนมัติ"""
        print(f"Loading data from {filepath}...")
        if not os.path.exists(filepath):
            print(f"[ERROR] File not found: {filepath}")
            return
            
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        count = 0
        for doc in data.get("docs", []):
            doc_id = doc.get("doc_id")
            paragraphs_dict = {p["para_id"]: p["text"] for p in doc.get("paragraphs", [])}
            self.add_document(doc_id, paragraphs_dict)
            count += 1
            
        print(f"[SUCCESS] Loaded {count} documents into DocumentStore.")

    def get_paragraphs(self, doc_id: str) -> Dict[str, str]:
        """ดึง text ทั้งหมดของ doc_id"""
        return self.texts.get(doc_id, {})

    def get_embeddings(self, doc_id: str) -> Dict[str, np.ndarray]:
        """ดึง embeddings ทั้งหมดของ doc_id (cached)"""
        return self.embeddings.get(doc_id, {})

    def get_text(self, doc_id: str, para_id: str) -> str:
        """ดึง text ของ paragraph ที่ระบุ"""
        return self.texts.get(doc_id, {}).get(para_id, "")

# Global instance
document_store = DocumentStore()
