from typing import Dict, Any, List
from pydantic import BaseModel, Field
import numpy as np
import os
from .document_store import document_store
from .embedder import embedder
from .llm_clients import llm_client

try:
    from rank_bm25 import BM25Okapi
    HAS_BM25 = True
except ImportError:
    HAS_BM25 = False
    print("[WARN] rank_bm25 not installed. Hybrid Search disabled. Run 'pip install rank_bm25'")

# -------------------------------------------------------------------
# Pydantic Schemas
# -------------------------------------------------------------------
# --- LLM Pass 1: เลือกก้อนที่ดีที่สุด ---
class AnchorSelection(BaseModel):
    reasoning: str = Field(description="Analysis of which group best answers the query")
    best_group_id: int = Field(description="The 1-indexed ID of the best group")

# --- LLM Pass 2: กรองเอาแค่เนื้อที่ต้องใช้สรุป ---
class FinalFilterOutput(BaseModel):
    reasoning: str = Field(description="Why these paragraphs were selected for the summary")
    selected_refs: List[str] = Field(description="Final list of para_ids needed to answer the query")
    selected_context: str = Field(description="Combined text of selected paragraphs")

# -------------------------------------------------------------------
# LangGraph Node
# -------------------------------------------------------------------
def retriever_node(state: Dict[str, Any]) -> Dict[str, Any]:
    print("--- RUNNING AGENT A: RETRIEVER (2-Pass) ---")
    query = state.get("query", "")
    doc_id = state.get("doc_id", "")
    feedback = state.get("feedback", "")
    retry_count = state.get("retry_count", 0)
    
    # ดึงข้อมูลทั้งหมดใน doc_id ออกมาก่อน
    doc_texts = document_store.get_paragraphs(doc_id)
    if not doc_texts:
        print(f"[WARN] No paragraphs found for doc_id: {doc_id}")
        return {"context": "", "refs": []}
        
    all_para_ids = list(doc_texts.keys())
    
    context_text_for_llm = ""
    
    # ====================================================
    # NORMAL MODE (retry < 2)
    # ====================================================
    if retry_count < 2:
        
        # ==============================================
        # STAGE 1: Hybrid Search (bge-m3 + BM25)
        # ==============================================
        print(f"[INFO] Stage 1: Hybrid Search (bge-m3 + BM25)")
        
        query_vecs = embedder.encode_query(query)
        doc_embeddings = document_store.get_embeddings(doc_id)
        q_vec = query_vecs.get("bge-m3")
        
        # --- BM25 Setup ---
        bm25_scores = {}
        if HAS_BM25 and all_para_ids:
            tokenized_corpus = [str(doc_texts[p]).split(" ") for p in all_para_ids]
            bm25 = BM25Okapi(tokenized_corpus)
            tokenized_query = str(query).split(" ")
            raw_bm25_scores = bm25.get_scores(tokenized_query)
            max_bm25 = max(raw_bm25_scores) if len(raw_bm25_scores) > 0 and max(raw_bm25_scores) > 0 else 1
            for idx, p_id in enumerate(all_para_ids):
                bm25_scores[p_id] = raw_bm25_scores[idx] / max_bm25
        
        # --- Scoring: bge-m3 (70%) + BM25 (30%) ---
        scores = {}
        if doc_embeddings and q_vec is not None:
            for p_id, p_vecs in doc_embeddings.items():
                vec_score = float(np.dot(q_vec, p_vecs))
                bm25_score = bm25_scores.get(p_id, 0.0)
                final_score = (vec_score * 0.7) + (bm25_score * 0.3)
                scores[p_id] = final_score
        
        if not scores:
            print("[WARN] No embeddings found. Falling back to word overlap.")
            query_words = set(str(query).split())
            for p_id in all_para_ids:
                text_words = set(str(doc_texts[p_id]).split())
                scores[p_id] = len(query_words & text_words)
        
        # --- Top 5 ---
        top_k = 5
        sorted_paras = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        print(f"  [bge-m3+BM25 Top5]: {[f'{p}({s:.3f})' for p, s in sorted_paras]}")
        
        # ==============================================
        # STAGE 2: จัดกลุ่ม Top 5 เป็น Clusters
        # ==============================================
        # แปลง para_id → index ในเอกสาร แล้วเรียงตามลำดับ
        top_indices = []
        for p_id, score in sorted_paras:
            try:
                idx = all_para_ids.index(p_id)
                top_indices.append((idx, p_id, score))
            except ValueError:
                pass
        top_indices.sort(key=lambda x: x[0])
        
        # จัดกลุ่ม: ถ้า index ห่างกันไม่เกิน 5 ถือว่าอยู่กลุ่มเดียวกัน
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
        
        print(f"  [Clusters]: {len(groups)} group(s) formed")
        
        # สร้างข้อความแสดงแต่ละกลุ่ม (ขยาย ±2 เพื่อให้ AI เห็นบริบทรอบๆ)
        group_texts = []
        group_anchor_ids = []
        for g_idx, group in enumerate(groups):
            min_idx = max(0, min(item[0] for item in group) - 2)
            max_idx = min(len(all_para_ids) - 1, max(item[0] for item in group) + 2)
            
            anchors = [item[1] for item in group]
            group_anchor_ids.append(anchors)
            
            lines = []
            for idx in range(min_idx, max_idx + 1):
                p_id = all_para_ids[idx]
                text = str(doc_texts.get(p_id, "")).strip()
                marker = " ← TOP" if p_id in anchors else ""
                lines.append(f"[{p_id}]: {text}{marker}")
            
            group_texts.append("\n".join(lines))
            print(f"    Group {g_idx + 1}: {all_para_ids[min_idx]}–{all_para_ids[max_idx]} (anchors: {anchors})")
        
        # ==============================================
        # STAGE 3: LLM Pass 1 — เลือกก้อนที่ดีที่สุด
        # ==============================================
        if len(groups) == 1:
            best_group_idx = 0
            print(f"[INFO] Stage 2: Only 1 cluster → skip anchor LLM call")
        else:
            print(f"[INFO] Stage 2: LLM Anchor Selection ({len(groups)} clusters)")
            
            groups_display = ""
            for g_idx, g_text in enumerate(group_texts):
                groups_display += f"\n=== GROUP {g_idx + 1} ===\n{g_text}\n"
            
            anchor_prompt = f"""You are an expert document analyst for Thai parliamentary meeting records.
You will receive a query and multiple GROUPS of paragraphs.
Your task: identify which GROUP contains the paragraphs that BEST and MOST DIRECTLY answer the query.

IMPORTANT:
- Focus on WHERE the actual answer content is, not just keyword matches.
- If the query asks about a specific agenda item (ระเบียบวาระที่ X), find the group that contains the discussion/content of that specific agenda item.

[Query]: {query}
{groups_display}
Which group number (1 to {len(groups)}) best answers the query?"""
            
            try:
                anchor_result = llm_client.generate_structured(anchor_prompt, AnchorSelection)
                if anchor_result:
                    best_group_idx = max(0, min(anchor_result.best_group_id - 1, len(groups) - 1))
                    print(f"  [Selected]: Group {best_group_idx + 1} (reason: {anchor_result.reasoning[:80]}...)")
                else:
                    best_group_idx = 0
                    print("[WARN] Anchor selection returned None, using group 1")
            except Exception as e:
                print(f"[WARN] Anchor selection failed: {e}, using group 1")
                best_group_idx = 0
        
        # ==============================================
        # STAGE 4: ขยายก้อนที่ชนะให้ครบเนื้อหา (-4 / +8)
        # ==============================================
        winner_group = groups[best_group_idx]
        anchor_min = min(item[0] for item in winner_group)
        anchor_max = max(item[0] for item in winner_group)
        
        expand_min = max(0, anchor_min - 4)
        expand_max = min(len(all_para_ids) - 1, anchor_max + 8)
        
        expanded_lines = []
        for idx in range(expand_min, expand_max + 1):
            p_id = all_para_ids[idx]
            text = str(doc_texts.get(p_id, "")).strip()
            expanded_lines.append(f"[{p_id}]: {text}")
        
        context_text_for_llm = "\n".join(expanded_lines)
        print(f"  [Expanded]: {all_para_ids[expand_min]}–{all_para_ids[expand_max]} ({expand_max - expand_min + 1} paragraphs)")
        
    else:
        # 🔴 BRUTE FORCE (Panic Mode)
        print(f"[INFO] Round {retry_count + 1} - BRUTE FORCE Mode! Reading entire document.")
        context_text_for_llm = "\n".join([f"[{p_id}]: {text}" for p_id, text in doc_texts.items()])
    
    # ==============================================
    # STAGE 5: LLM Pass 2 — กรองเอาแค่เนื้อที่ต้องใช้สรุป
    # ==============================================
    print(f"[INFO] Stage 3: LLM Meat Filter running...")
    feedback_str = f"\n[Feedback from Validator]: {feedback}" if feedback else ""
    
    skill_file_path = os.path.join("skills", "skill_retriever_ranker.md")
    system_instruction = ""
    try:
        with open(skill_file_path, "r", encoding="utf-8") as f:
            system_instruction = f.read()
    except Exception:
        system_instruction = "You are an expert paragraph selector. Select ONLY paragraphs needed to answer the query."
    
    prompt = f"""{system_instruction}

==================================================
YOUR CURRENT TASK:

[Query]: {query}
{feedback_str}
[Context Paragraphs to Evaluate]:
{context_text_for_llm}
==================================================
"""
    
    try:
        filter_output = llm_client.generate_structured(prompt, FinalFilterOutput)
        if filter_output:
            selected_refs = filter_output.selected_refs
            selected_context = filter_output.selected_context
            print(f"Agent A selected {len(selected_refs)} refs: {selected_refs}")
        else:
            selected_refs = all_para_ids[:3]
            selected_context = "Fallback context"
            print("[WARN] Agent A returned None, using fallback.")
            
    except Exception as e:
        print(f"[ERROR] Agent A Failed: {e}")
        selected_refs = all_para_ids[:3]
        selected_context = "Fallback context"
        
    return {
        "context": selected_context,
        "refs": selected_refs
    }