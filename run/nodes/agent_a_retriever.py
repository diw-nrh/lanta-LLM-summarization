from typing import Dict, Any, List
from pydantic import BaseModel, Field
import numpy as np
import os
import asyncio
from .document_store import document_store
from .embedder import embedder
# Removed unused llm_client import that causes vLLM multiprocessing crashes

try:
    from rank_bm25 import BM25Okapi
    HAS_BM25 = True
except ImportError:
    HAS_BM25 = False
    print("[WARN] rank_bm25 not installed. Hybrid Search disabled. Run 'pip install rank_bm25'")
try:
    from pythainlp.tokenize import word_tokenize
    def tokenize_thai(text: str) -> List[str]:
        return word_tokenize(str(text), engine="newmm", keep_whitespace=False)
except ImportError:
    print("[WARN] pythainlp not installed. Using character 3-grams for BM25 fallback.")
    def tokenize_thai(text: str) -> List[str]:
        t = str(text).replace(" ", "")
        return [t[i:i+3] for i in range(len(t)-2)] if len(t) > 2 else [t]

# -------------------------------------------------------------------
# Pydantic Schemas (CoT & Precision Extraction + 1-3 Scoring System)
# -------------------------------------------------------------------
class GroupExtraction(BaseModel):
    query_intent: str = Field(description="Step 1: อธิบายสั้นๆ ว่าจากคำถามนี้ เรากำลังตามหาข้อมูลประเภทใด และต้องการอะไร")
    irrelevant_paras_analysis: str = Field(description="Step 2: สแกนดูทีละ para_id แล้วระบุว่า para_id ไหนบ้างที่ไม่เกี่ยวข้อง หรือเป็นแค่น้ำ พร้อมบอกเหตุผลสั้นๆ ที่ต้องตัดทิ้ง")
    relevant_paras_analysis: str = Field(description="Step 3: สแกนหา para_id ที่มีคำตอบ **คำเตือน: หากย่อหน้าที่เจอเป็นเพียง 'หัวข้อ' (เช่น ระเบียบวาระที่...) คุณต้องดึงย่อหน้าถัดไปที่อธิบายเนื้อหาของหัวข้อนั้นมาด้วยเสมอ ห้ามดึงมาแค่หัวข้อเด็ดขาด**")
    
    # 🌟 เปลี่ยนจาก bool เป็นให้คะแนน 1-3 เพื่อบังคับให้คิดละเอียดขึ้น
    answer_score: int = Field(description="Step 4: ประเมินคะแนน 1 ถึง 3. (1 = ไม่มีคำตอบเลย, 2 = มีการกล่าวถึงหัวข้อ/คีย์เวิร์ด แต่ยังไม่ใช่คำตอบที่สมบูรณ์, 3 = มีคำตอบที่ชัดเจน สมบูรณ์ และตรงคำถามเป๊ะๆ)")
    
    extracted_refs: List[str] = Field(description="Step 5: รหัส para_id ที่เป็นคำตอบ โดยต้องเลือก 'หัวข้อหลัก' + 'เนื้อหาที่เกี่ยวข้องทั้งหมด' มาเป็นชุด (เช่น ['P48', 'P49', 'P50']) หาก score เป็น 1 ให้คืนค่าเป็นลิสต์ว่าง []")

class FinalWinnerSelection(BaseModel):
    analysis: str = Field(description="วิเคราะห์และเปรียบเทียบจุดเด่นของแต่ละกลุ่มที่เข้ารอบ ว่ากลุ่มใดให้คำตอบได้ตรงประเด็น สมบูรณ์ และถูกต้องที่สุด")
    best_group_idx: int = Field(description="กรอกเฉพาะตัวเลข Index ของกลุ่มที่ชนะ (เช่น 1, 2 หรือ 3)")

# -------------------------------------------------------------------
# LangGraph Node (Async)
# -------------------------------------------------------------------
async def retriever_node(state: Dict[str, Any]) -> Dict[str, Any]:
    print("--- RUNNING AGENT A: RETRIEVER (Precision Extractor + CoT Scoring) ---")
    query = state.get("query", "")
    doc_id = state.get("doc_id", "")
    
    doc_texts = document_store.get_paragraphs(doc_id)
    if not doc_texts:
        print(f"[WARN] No paragraphs found for doc_id: {doc_id}")
        return {"context": "", "refs": []}
        
    import re
    all_para_ids = [p for p in doc_texts.keys() if re.match(r'^P\d+$', str(p).strip())]
    
    # ==============================================
    # STAGE 1: Hybrid Search (bge-m3 + BM25)
    # ==============================================
    print(f"[INFO] Stage 1: Hybrid Search (bge-m3 + BM25)")
    query_vecs = embedder.encode_query(query)
    doc_embeddings = document_store.get_embeddings(doc_id)
    q_vec = query_vecs.get("bge-m3")
    
    bm25_scores = {}
    if HAS_BM25 and all_para_ids:
        bm25, _ = document_store.get_bm25(doc_id)
        if bm25:
            tokenized_query = str(query).split(" ")
            raw_bm25_scores = bm25.get_scores(tokenized_query)
            max_bm25 = max(raw_bm25_scores) if len(raw_bm25_scores) > 0 and max(raw_bm25_scores) > 0 else 1
            for idx, p_id in enumerate(all_para_ids):
                bm25_scores[p_id] = raw_bm25_scores[idx] / max_bm25
    
    scores = {}
    if doc_embeddings and q_vec is not None:
        for p_id, p_vecs in doc_embeddings.items():
            vec_score = float(np.dot(q_vec, p_vecs))
            bm25_score = bm25_scores.get(p_id, 0.0)
            final_score = (vec_score * 0.5) + (bm25_score * 0.5)
            scores[p_id] = final_score
    
    if not scores:
        query_words = set(tokenize_thai(query))

        for p_id in all_para_ids:
            text_words = set(tokenize_thai(doc_texts[p_id]))
            scores[p_id] = len(query_words & text_words)
    
    top_k = 15
    sorted_paras = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    
    # ==============================================
    # STAGE 2: จัดกลุ่ม Top K เป็น Clusters
    # ==============================================
    top_indices = []
    for p_id, score in sorted_paras:
        try:
            idx = all_para_ids.index(p_id)
            top_indices.append((idx, p_id, score))
        except ValueError:
            pass
    top_indices.sort(key=lambda x: x[0])
    
    groups = []
    if top_indices:
        current_group = [top_indices[0]]
        for i in range(1, len(top_indices)):
            # ขยายระยะห่างในการ Group จาก 5 เป็น 10 (ลดจาก 20 เพื่อไม่ให้ Context บวมจนโมเดลหลอน)
            if top_indices[i][0] - current_group[-1][0] <= 10:
                current_group.append(top_indices[i])
            else:
                groups.append(current_group)
                current_group = [top_indices[i]]
        groups.append(current_group)
    
    group_texts = []
    group_refs_list = []
    for g_idx, group in enumerate(groups):
        min_idx = max(0, min(item[0] for item in group) - 1)
        # เผื่อความยาวด้านล่างเพิ่มขึ้น (จาก +10 กลับมาเป็น +5 พอ เพื่อกัน LLM หลอน)
        max_idx = min(len(all_para_ids) - 1, max(item[0] for item in group) + 5)
        
        lines = []
        refs = []
        for idx in range(min_idx, max_idx + 1):
            p_id = all_para_ids[idx]
            refs.append(p_id)
            text = str(doc_texts.get(p_id, "")).strip()
            lines.append(f"[{p_id}]: {text}")
        group_texts.append("\n".join(lines))
        group_refs_list.append(refs)
    
    # ==============================================
    # STAGE 3 & 4: Reranker Selection (Replacing LLM)
    # ==============================================
    top_n_eval = 5
    eval_groups = group_texts[:top_n_eval]
    eval_refs = group_refs_list[:top_n_eval]
    
    final_refs = []
    from .embedder import reranker
    
    if reranker.ready and eval_groups:
        print(f"[INFO] Using Cross-Encoder Reranker for selection on {len(eval_groups)} groups...")
        scores = reranker.compute_scores(query, eval_groups)
        best_idx = int(np.argmax(scores))
        final_refs = eval_refs[best_idx]
        print(f"✅ Reranker selected Group {best_idx+1} with score {scores[best_idx]:.4f}")
    else:
        print("[WARN] Reranker not ready or no groups -> ลองสุ่มมโน 1 Paragraph ที่คะแนน Vector สูงสุด")
        final_refs = [p_id for p_id, score in sorted_paras[:1]]
    
    # สร้าง Text Context จาก Ref สุดท้ายที่ชนะ
    selected_lines = []
    for p_id in final_refs:
        if p_id in doc_texts:
            selected_lines.append(f"[{p_id}]: {doc_texts[p_id]}")
    selected_context = "\n".join(selected_lines) if selected_lines else "ไม่พบข้อมูล"

    print(f"✅ Final Refs Selected: {final_refs}")
    
    return {
        "context": selected_context,
        "refs": final_refs
    }