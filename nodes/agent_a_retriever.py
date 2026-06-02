from typing import Dict, Any, List
from pydantic import BaseModel, Field
import numpy as np
import os
import asyncio
from .document_store import document_store
from .embedder import embedder
from .llm_clients import llm_client

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
        
    all_para_ids = list(doc_texts.keys())
    
    # ==============================================
    # STAGE 1: Hybrid Search (bge-m3 + BM25)
    # ==============================================
    print(f"[INFO] Stage 1: Hybrid Search (bge-m3 + BM25)")
    query_vecs = embedder.encode_query(query)
    doc_embeddings = document_store.get_embeddings(doc_id)
    q_vec = query_vecs.get("bge-m3")
    
    bm25_scores = {}
    if HAS_BM25 and all_para_ids:
        tokenized_corpus = [tokenize_thai(doc_texts[p]) for p in all_para_ids]
        bm25 = BM25Okapi(tokenized_corpus)
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
            final_score = (vec_score * 0.7) + (bm25_score * 0.3)
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
            if top_indices[i][0] - current_group[-1][0] <= 5:
                current_group.append(top_indices[i])
            else:
                groups.append(current_group)
                current_group = [top_indices[i]]
        groups.append(current_group)
    
    group_texts = []
    for g_idx, group in enumerate(groups):
        min_idx = max(0, min(item[0] for item in group) - 3)
        max_idx = min(len(all_para_ids) - 1, max(item[0] for item in group) + 4)
        
        lines = []
        for idx in range(min_idx, max_idx + 1):
            p_id = all_para_ids[idx]
            text = str(doc_texts.get(p_id, "")).strip()
            lines.append(f"[{p_id}]: {text}")
        group_texts.append("\n".join(lines))
    
    # ==============================================
    # STAGE 3: สกัดหา Paragraph ที่ใช่ (Extract Refs) ด้วย CoT
    # ==============================================
    top_n_eval = 15
    eval_groups = group_texts[:top_n_eval]
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    skill_file_path = os.path.abspath(os.path.join(current_dir, "..", "skills", "skill_retriever_ranker.md"))
    try:
        with open(skill_file_path, "r", encoding="utf-8") as f:
            system_instruction = f.read()
    except Exception:
        system_instruction = "You are a precision paragraph filter for Thai parliamentary meeting records."

    prompts_list = []
    for g_idx, g_text in enumerate(eval_groups):
        # 🌟 อัปเดต Prompt ให้สอดคล้องกับระบบคะแนน 1-3
        prompt = f"""{system_instruction}

[Query]: {query}
[Context Group {g_idx + 1}]:
{g_text}

CRITICAL INSTRUCTIONS FOR JSON OUTPUT:
- DO NOT output placeholder text. You MUST generate actual analysis based on the text above.
- BE CAREFUL with Thai numerals (๑,๒,๓,๔,๕,๖,๗,๘,๙,๐) vs Arabic numerals (1,2,3...). Do not misread '๑๔' as '๑ไ' or '๒๕๖๗' as '๒๕ๆ๗'.

TASK (Step-by-Step Elimination):
1. query_intent: What exactly is the query asking for?
2. irrelevant_paras_analysis: Which paragraphs are irrelevant, just headers, or do not contain the answer? Why? (Exclude them)
3. relevant_paras_analysis: Which specific paragraphs ACTUALLY contain the exact answer? Why? (If none, state that clearly)
4. answer_score: Rate the group from 1 to 3. 
   - 1 = No answer at all.
   - 2 = Mentions the topic/keywords, but the answer is incomplete or missing.
   - 3 = Contains the exact, complete answer to the query.
5. extracted_refs: Provide the precise list of paragraph IDs (e.g., ["P5", "P6"]). If score is 1, you MUST output []."""
        prompts_list.append(prompt)
        
    tasks = [llm_client.agenerate_structured(p, GroupExtraction) for p in prompts_list]
    batch_results = await asyncio.gather(*tasks)
    
    # 🌟 แยกระดับกลุ่มตามคะแนนที่ได้ (3 และ 2)
    valid_groups_score_3 = []
    valid_groups_score_2 = []
    
    for g_idx, res in enumerate(batch_results):
        if res and res.extracted_refs and res.answer_score > 1:
            group_info = {
                "g_idx": g_idx, 
                "display_id": g_idx + 1,
                "refs": res.extracted_refs,
                "reasoning": f"Intent: {res.query_intent}\nIrrelevant: {res.irrelevant_paras_analysis}\nRelevant: {res.relevant_paras_analysis}\nScore: {res.answer_score}"
            }
            if res.answer_score == 3:
                valid_groups_score_3.append(group_info)
            elif res.answer_score == 2:
                valid_groups_score_2.append(group_info)

    # 🌟 เลือกว่าจะส่งกลุ่มไหนเข้าชิง (ให้ความสำคัญกับกลุ่มที่ได้ 3 ก่อน)
    if valid_groups_score_3:
        valid_groups = valid_groups_score_3
        print(f"Query : {query} \nDEBUG (Found groups with score 3) : {len(valid_groups)}")
    elif valid_groups_score_2:
        valid_groups = valid_groups_score_2
        print(f"Query : {query} \nDEBUG (No score 3 found. Using score 2 fallback) : {len(valid_groups)}")
    else:
        valid_groups = []
        print(f"Query : {query} \nDEBUG (No valid groups found)")
    
    # ==============================================
    # STAGE 4: ตัดสินผู้ชนะ (Final Selection)
    # ==============================================
    final_refs = []
    anchor_result = None 
    
    if not valid_groups:
        print("[WARN] ไม่มีกลุ่มไหนมีคำตอบเลย (All Zeros/Ones) -> ใช้ Fallback จาก Vector Search")
        final_refs = [p_id for p_id, score in sorted_paras[:3]]
    elif len(valid_groups) == 1:
        print("[INFO] มีเพียง 1 กลุ่มที่ตอบได้ดีที่สุด -> ใช้งานทันที")
        final_refs = valid_groups[0]["refs"]
    else:
        print(f"[INFO] มีเข้าชิง {len(valid_groups)} กลุ่ม -> ส่งให้ LLM ฟันธง")
        groups_display = ""
        for vg in valid_groups:
            extracted_text_lines = []
            for p_id in vg["refs"]:
                if p_id in doc_texts:
                    extracted_text_lines.append(f"[{p_id}]: {doc_texts[p_id]}")
            actual_text = "\n".join(extracted_text_lines)
            
            groups_display += f"\n=== GROUP {vg['display_id']} (Extracted Refs: {vg['refs']}) ===\n"
            groups_display += f"Reasoning from Stage 3:\n{vg['reasoning']}\n"
            groups_display += f"Actual Text Content:\n{actual_text}\n"
        
        anchor_prompt = f"""You are an expert document analyst.
[Query]: {query}

Multiple candidate groups claim to have the answer. 
Here are their analyses and the ACTUAL TEXT they extracted:
{groups_display}

Perform a step-by-step evaluation (Chain of Thought):
1. Read the 'Actual Text Content' of each candidate group carefully.
2. Determine which group provides the most complete and direct answer to the query.
3. Output the exact group ID (e.g., if GROUP 2 is best, output 2)."""
        
        try:
            anchor_result = await llm_client.agenerate_structured(anchor_prompt, FinalWinnerSelection)
            if anchor_result:
                best_display_id = anchor_result.best_group_idx
                winning_vg = next((vg for vg in valid_groups if vg["display_id"] == best_display_id), valid_groups[0])
                final_refs = winning_vg["refs"]
            else:
                final_refs = valid_groups[0]["refs"]
        except Exception as e:
            print(f"[ERROR] Final selection failed: {e}")
            final_refs = valid_groups[0]["refs"]
            
        print(f"Query : {query} \nDEBUG (anchor_result) : {anchor_result}")
    
    # สร้าง Text Context จาก Ref สุดท้ายที่ชนะ
    selected_lines = []
    for p_id in final_refs:
        if p_id in doc_texts:
            selected_lines.append(f"[{p_id}]: {doc_texts[p_id]}")
    selected_context = "\n".join(selected_lines) if selected_lines else "ไม่พบข้อมูลที่ต้องการ"

    print(f"✅ Final Refs Selected: {final_refs}")
    
    return {
        "context": selected_context,
        "refs": final_refs
    }