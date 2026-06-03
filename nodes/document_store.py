import numpy as np
from typing import Dict
import json
import os

class DocumentStore:
    def __init__(self):
        # Format: {doc_id: {para_id: text}}
        self.texts: Dict[str, Dict[str, str]] = {}
        self.embeddings: Dict[str, Dict[str, np.ndarray]] = {}
        self.bm25_indexes = {}
        self.tokenized_corpus_dict = {}

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
        try:
            from rank_bm25 import BM25Okapi
            from pythainlp.tokenize import word_tokenize
            def tokenize_thai(text: str) -> list[str]:
                return word_tokenize(str(text), engine="newmm", keep_whitespace=False)
            has_bm25 = True
        except ImportError:
            has_bm25 = False
            print("[WARN] BM25 or pythainlp not installed. BM25 Caching disabled.")
            
        self.bm25_indexes = {}
        self.tokenized_corpus_dict = {}

        for doc in data.get("docs", []):
            doc_id = doc.get("doc_id")
            paragraphs_dict = {p["para_id"]: p["text"] for p in doc.get("paragraphs", [])}
            self.add_document(doc_id, paragraphs_dict)
            
            # Pre-compute BM25
            if has_bm25:
                para_ids = list(paragraphs_dict.keys())
                tokenized_corpus = [tokenize_thai(paragraphs_dict[p]) for p in para_ids]
                self.bm25_indexes[doc_id] = BM25Okapi(tokenized_corpus)
                self.tokenized_corpus_dict[doc_id] = tokenized_corpus
                
            count += 1
            
        print(f"[SUCCESS] Loaded {count} documents into DocumentStore and Cached BM25.")

    def get_paragraphs(self, doc_id: str) -> Dict[str, str]:
        """ดึง text ทั้งหมดของ doc_id"""
        return self.texts.get(doc_id, {})

    def get_embeddings(self, doc_id: str) -> Dict[str, np.ndarray]:
        """ดึง embeddings ทั้งหมดของ doc_id (cached)"""
        return self.embeddings.get(doc_id, {})

    def get_text(self, doc_id: str, para_id: str) -> str:
        """ดึง text ของ paragraph ที่ระบุ"""
        return self.texts.get(doc_id, {}).get(para_id, "")

    def get_bm25(self, doc_id: str):
        return self.bm25_indexes.get(doc_id), self.tokenized_corpus_dict.get(doc_id)

# Global instance
document_store = DocumentStore()
