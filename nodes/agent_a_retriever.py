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

# -------------------------------------------------------------------
# Pydantic Schemas
# -------------------------------------------------------------------
class GroupEvaluation(BaseModel):
    reasoning: str = Field(description="Analysis of how well this group answers the query")
    score: int = Field(description="Score from 1 to 10. 10 = perfectly contains the exact answer, 1 = irrelevant or just headers")

class AnchorSelection(BaseModel):
    reasoning: str = Field(description="Analysis of which group best answers the query among the top choices")
    best_group_id: int = Field(description="The 1-indexed ID of the best group")

class FinalFilterOutput(BaseModel):
    reasoning: str = Field(description="Step-by-step analysis of each provided paragraph to determine if it should be selected.")
    selected_refs: List[str] = Field(description="Final list of para_ids needed to answer the query")

# -------------------------------------------------------------------
# LangGraph Node (Async)
# -------------------------------------------------------------------
async def retriever_node(state: Dict[str, Any]) -> Dict[str, Any]:
    print("--- RUNNING AGENT A: RETRIEVER (2-Pass) ---")
    query = state.get("query", "")
    doc_id = state.get("doc_id", "")
    
    doc_texts = document_store.get_paragraphs(doc_id)
    if not doc_texts:
        print(f"[WARN] No paragraphs found for doc_id: {doc_id}")
        return {"context": "", "refs": []}
        
    all_para_ids = list(doc_texts.keys())
    context_text_for_llm = ""
    
    if True:
        # ==============================================
        # STAGE 1: Hybrid Search (bge-m3 + BM25)
        # ==============================================
        print(f"[INFO] Stage 1: Hybrid Search (bge-m3 + BM25)")
        query_vecs = embedder.encode_query(query)
        doc_embeddings = document_store.get_embeddings(doc_id)
        q_vec = query_vecs.get("bge-m3")
        
        bm25_scores = {}
        if HAS_BM25 and all_para_ids:
            tokenized_corpus = [str(doc_texts[p]).split(" ") for p in all_para_ids]
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
            query_words = set(str(query).split())
            for p_id in all_para_ids:
                text_words = set(str(doc_texts[p_id]).split())
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
        
        # ==============================================
        # STAGE 3: LLM Pass 1 — ประเมินและให้คะแนน (BATCHING)
        # ==============================================
        if len(groups) == 1:
            best_group_idx = 0
        else:
            prompts_list = []
            for g_idx, g_text in enumerate(group_texts):
                prompt = f"""You are a strict document evaluator for Thai parliamentary meeting records.
[Query]: {query}

[Context Group {g_idx + 1}]:
{g_text}

Evaluate how well this exact group of paragraphs answers the query.
Be extremely strict. If this group only contains the agenda title (e.g. "ระเบียบวาระที่ X") but DOES NOT contain the actual substance or answer, score it VERY LOW (1-3).
If it contains the actual substantive answer, score it HIGH (8-10).

Give your reasoning and then a final score from 1 to 10."""
                prompts_list.append(prompt)
                
            # ส่งเข้า vLLM รวดเดียวแบบ Async
            tasks = [llm_client.agenerate_structured(p, GroupEvaluation) for p in prompts_list]
            batch_results = await asyncio.gather(*tasks)
            
            eval_results = []
            for g_idx, res in enumerate(batch_results):
                if res:
                    eval_results.append((g_idx, res.score, res.reasoning))
                else:
                    eval_results.append((g_idx, 0, "Error parsing JSON from LLM"))
            
            eval_results.sort(key=lambda x: x[1], reverse=True)
            
            top_3 = eval_results[:3]
            if len(top_3) == 1:
                best_group_idx = top_3[0][0]
            else:
                groups_display = ""
                for r in top_3:
                    g_idx = r[0]
                    groups_display += f"\n=== GROUP {g_idx + 1} ===\n{group_texts[g_idx]}\n"
                
                anchor_prompt = f"""You are an expert document analyst for Thai parliamentary meeting records.
[Query]: {query}

Here are the Top {len(top_3)} candidate groups of paragraphs:
{groups_display}

Which group number best and most directly answers the query?
Select the exact group number (e.g. if you select 'GROUP 4', output 4).
Give your reasoning and then the best_group_id."""
                try:
                    # รอผลลัพธ์จาก vLLM
                    anchor_result = await llm_client.agenerate_structured(anchor_prompt, AnchorSelection)
                    if anchor_result:
                        best_group_idx = max(0, min(anchor_result.best_group_id - 1, len(groups) - 1))
                        if not any(r[0] == best_group_idx for r in top_3):
                            best_group_idx = top_3[0][0]
                    else:
                        best_group_idx = top_3[0][0]
                except Exception as e:
                    best_group_idx = top_3[0][0]
        
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
    
    # ==============================================
    # STAGE 5: LLM Pass 2 — กรองเอาแค่เนื้อที่ต้องใช้สรุป
    # ==============================================
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    skill_file_path = os.path.abspath(os.path.join(current_dir, "..", "skills", "skill_retriever_ranker.md"))
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
[Context Paragraphs to Evaluate]:
{context_text_for_llm}
==================================================
"""
    
    try:
        filter_output = await llm_client.agenerate_structured(prompt, FinalFilterOutput)
        if filter_output:
            selected_refs = filter_output.selected_refs
            selected_lines = []
            for p_id in selected_refs:
                if p_id in doc_texts:
                    selected_lines.append(f"[{p_id}]: {doc_texts[p_id]}")
            selected_context = "\n".join(selected_lines) if selected_lines else "ไม่พบข้อมูลที่ต้องการ"
        else:
            selected_refs = all_para_ids[:3]
            selected_context = "Fallback context"
            
    except Exception as e:
        print(f"[ERROR] Agent A Failed: {e}")
        selected_refs = all_para_ids[:3]
        selected_context = "Fallback context"
    
    print("[DEBUG] : " ,selected_context,"[refs] :",selected_refs)
        
    return {
        "context": selected_context,
        "refs": selected_refs
    }