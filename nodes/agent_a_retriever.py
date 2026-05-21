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
# Pydantic Schemas for LLM Structured Output
# -------------------------------------------------------------------
# --- STAGE 0 Schema ---
class EnsembleWeights(BaseModel):
    e5_large_weight: float = Field(description="Weight for e5-large model (0.0 to 1.0)")
    bge_m3_weight: float = Field(description="Weight for bge-m3 model (0.0 to 1.0)")
    wangchanberta_weight: float = Field(description="Weight for WangchanBERTa model (0.0 to 1.0)")

class EnsembleOutput(BaseModel):
    thought_process: str = Field(description="Analysis of the query and justification for weights")
    weights: EnsembleWeights

# --- STAGE 2 Schema ---
class ThoughtProcess(BaseModel):
    query_analysis: str = Field(description="Analyze the query")
    context_understanding: str = Field(description="Analyze the context window")
    key_findings: str = Field(description="Key findings from paragraphs")
    self_correction: str = Field(description="Self correction notes")
    final_reasoning: str = Field(description="Final reasoning for selection")

class ParagraphDecision(BaseModel):
    para_id: str = Field(description="ID of the paragraph")
    para_type: str = Field(description="Type: Resolution/Discussion/Announcement/Agenda/General")
    contains_entity: str = Field(description="Yes/No/Partial")
    answers_directly: str = Field(description="Yes/No/Implicit")
    is_contiguous: str = Field(description="Yes/No")
    score: int = Field(description="Score 0-100")
    decision: str = Field(description="Yes/Maybe/No")

class ContiguousBlock(BaseModel):
    block_id: str = Field(description="ID of the block")
    para_ids: List[str] = Field(description="List of para_ids in this block")
    is_valid: bool = Field(description="Is this a valid contiguous block?")

class RankerOutput(BaseModel):
    thought_process: ThoughtProcess
    paragraph_decisions: List[ParagraphDecision]
    contiguous_blocks: List[ContiguousBlock]
    selected_refs: List[str] = Field(description="List of para_ids that scored Yes")
    selected_context: str = Field(description="Combined text of selected paragraphs")

# -------------------------------------------------------------------
# LangGraph Node
# -------------------------------------------------------------------
def retriever_node(state: Dict[str, Any]) -> Dict[str, Any]:
    print("--- RUNNING AGENT A: RETRIEVER (WITH ENSEMBLE) ---")
    query = state.get("query", "")
    doc_id = state.get("doc_id", "")
    feedback = state.get("feedback", "")
    retry_count = state.get("retry_count", 0)
    
    context_text_for_llm = ""
    all_para_ids = []
    
    # ดึงข้อมูลทั้งหมดใน doc_id ออกมาก่อน
    doc_texts = document_store.get_paragraphs(doc_id)
    if not doc_texts:
        print(f"[WARN] No paragraphs found for doc_id: {doc_id}")
        return {"context": "", "refs": []}
        
    all_para_ids = list(doc_texts.keys())
    
    # ====================================================
    # SMART FALLBACK & ENSEMBLE LOGIC
    # ====================================================
    if retry_count < 2:
        # 🟢 STAGE 0: ENSEMBLE ANALYZER (LLM Call 1)
        print(f"[INFO] Round {retry_count + 1} - Stage 0: Ensemble Analyzer running...")
        
        ensemble_skill_path = os.path.join("skills", "skill_ensemble.md")
        ensemble_instruction = ""
        try:
            with open(ensemble_skill_path, "r", encoding="utf-8") as f:
                ensemble_instruction = f.read()
        except Exception:
            ensemble_instruction = "You are an Ensemble Engineer."
            
        ensemble_prompt = f"""{ensemble_instruction}
        
==================================================
YOUR CURRENT TASK: Analyze this query and provide weights.
[Query]: {query}
==================================================
"""
        # Default weights in case LLM fails
        w_e5, w_bge, w_wangchan = 0.33, 0.34, 0.33
        
        try:
            ensemble_out = llm_client.generate_structured(ensemble_prompt, EnsembleOutput)
            if ensemble_out:
                w_e5 = ensemble_out.weights.e5_large_weight
                w_bge = ensemble_out.weights.bge_m3_weight
                w_wangchan = ensemble_out.weights.wangchanberta_weight
                print(f"[INFO] Ensemble Weights -> e5: {w_e5:.2f}, bge: {w_bge:.2f}, wangchan: {w_wangchan:.2f}")
        except Exception as e:
            print(f"[WARN] Ensemble Analyzer failed, using defaults. Error: {e}")

        # 🟢 STAGE 1: HYBRID SEARCH (Vector + BM25)
        print(f"[INFO] Stage 1: Hybrid Search Mode activated.")
        
        query_vecs = embedder.encode_query(query)
        doc_embeddings = document_store.get_embeddings(doc_id)
        
        # สมมติว่าในอนาคต embedder.py ส่งกลับมา 3 โมเดล 
        q_vec_bge = query_vecs.get("bge-m3")
        q_vec_e5 = query_vecs.get("e5-large", q_vec_bge) 
        q_vec_wangchan = query_vecs.get("wangchanberta", q_vec_bge)
        
        # --- BM25 Setup ---
        bm25_scores = {}
        if HAS_BM25 and all_para_ids:
            # ใช้ str() คลุมป้องกันบั๊ก TypeError หากบางย่อหน้าใน JSON ถูกแปลงเป็นตัวเลข
            tokenized_corpus = [str(doc_texts[p]).split(" ") for p in all_para_ids]
            bm25 = BM25Okapi(tokenized_corpus)
            tokenized_query = str(query).split(" ")
            raw_bm25_scores = bm25.get_scores(tokenized_query)
            
            # Normalize BM25 scores (Min-Max to 0-1)
            # แก้ไขจาก .size เป็น len() ป้องกันบั๊กกรณี rank_bm25 คืนค่าเป็น Python List ปกติ
            max_bm25 = max(raw_bm25_scores) if len(raw_bm25_scores) > 0 and max(raw_bm25_scores) > 0 else 1
            for idx, p_id in enumerate(all_para_ids):
                bm25_scores[p_id] = raw_bm25_scores[idx] / max_bm25
                
        scores = {}
        if doc_embeddings:
            for p_id, p_vecs in doc_embeddings.items():
                score_bge = np.dot(q_vec_bge, p_vecs) if q_vec_bge is not None else 0
                score_e5 = np.dot(q_vec_e5, p_vecs) if q_vec_e5 is not None else 0
                score_wangchan = np.dot(q_vec_wangchan, p_vecs) if q_vec_wangchan is not None else 0
                
                vector_score = (score_e5 * w_e5) + (score_bge * w_bge) + (score_wangchan * w_wangchan)
                
                # Hybrid Fusion
                bm25_score = bm25_scores.get(p_id, 0.0)
                # Weight: 70% Vector, 30% BM25
                final_score = (vector_score * 0.7) + (bm25_score * 0.3)
                
                scores[p_id] = float(final_score)
                
        # เลือก Top 15 Paragraphs เฉพาะในเอกสารนี้
        top_k = 15
        sorted_paras = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        
        paragraphs_to_read = []
        for p_id, score in sorted_paras:
            try:
                idx = all_para_ids.index(p_id)
                prev_text = str(doc_texts[all_para_ids[idx-1]]) + " " if idx > 0 else ""
                curr_text = str(doc_texts[p_id])
                next_text = " " + str(doc_texts[all_para_ids[idx+1]]) if idx < len(all_para_ids) - 1 else ""
                window_text = prev_text + curr_text + next_text
            except ValueError:
                window_text = str(doc_texts.get(p_id, ""))
                
            paragraphs_to_read.append(f"[{p_id}]: {window_text.strip()}")
            
        context_text_for_llm = "\n".join(paragraphs_to_read)
        
    else:
        # 🔴 ROUND 3: Brute Force (Panic Mode)
        print(f"[INFO] Round {retry_count + 1} - Final Retry. BRUTE FORCE Mode activated! Reading entire document.")
        context_text_for_llm = "\n".join([f"[{p_id}]: {text}" for p_id, text in doc_texts.items()])
    
    # ----------------------------------------------------
    # STAGE 2: LLM Ranker (Agent A - LLM Call 2)
    # ----------------------------------------------------
    print(f"[INFO] Stage 2: LLM Ranker running...")
    feedback_str = f"Feedback from Validator (Retry): {feedback}" if feedback else "No feedback. Initial run."
    
    skill_file_path = os.path.join("skills", "skill_retriever_ranker.md")
    system_instruction = ""
    try:
        with open(skill_file_path, "r", encoding="utf-8") as f:
            system_instruction = f.read()
    except Exception as e:
        print(f"[WARN] Could not load skill file: {e}")
        system_instruction = "You are an expert Retriever and Ranker."
    
    prompt = f"""{system_instruction}

==================================================
YOUR CURRENT TASK:

[Query]: {query}
[System State]: {feedback_str}

[Context Paragraphs to Evaluate]:
{context_text_for_llm}
==================================================
"""
    
    try:
        ranker_output = llm_client.generate_structured(prompt, RankerOutput)
        if ranker_output:
            selected_refs = ranker_output.selected_refs
            selected_context = ranker_output.selected_context
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