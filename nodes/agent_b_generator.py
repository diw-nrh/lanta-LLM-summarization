from typing import Dict, Any, List
from pydantic import BaseModel, Field
import os
import re
from .llm_clients import llm_client

def clean_format(text: str) -> str:
    if not text: return ""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    text = re.sub(r'^(สรุปคือ|คำตอบคือ|สรุปได้ว่า)\s*', '', text)
    text = re.sub(r'(ครับ|ค่ะ)$', '', text).strip()
    return text

class PreFilterExtraction(BaseModel):
    reasoning: str = Field(description="Briefly explain your selection in 1-2 sentences. Do not evaluate every single paragraph to save tokens.")
    relevant_refs: List[str] = Field(description="List ONLY the specific paragraph IDs (e.g., P5, P6) that contain the direct answer. Be extremely strict.")

async def generator_node(state: Dict[str, Any]) -> Dict[str, Any]:
    print("--- RUNNING AGENT B: TWO-STEP HYBRID PIPELINE ---")
    query = state.get("query", "")
    context = state.get("context", "")
    retriever_refs = state.get("refs", [])
    print("[DEBUG] context : ",context)
    if context == "ไม่พบคำตอบ":
        return {
            "abstractive": "ไม่พบคำตอบ",
            "used_refs": retriever_refs
        }

    current_dir = os.path.dirname(os.path.abspath(__file__))
    skill_file_path = os.path.abspath(os.path.join(current_dir, "..", "skills", "skill_generator.md"))
    try:
        with open(skill_file_path, "r", encoding="utf-8") as f:
            system_instruction = f.read().strip()
    except Exception as e:
        system_instruction = "คุณคือผู้ช่วยที่เชี่ยวชาญในการสรุปเนื้อหาจากเอกสาร ตอบเป็นภาษาไทยเท่านั้น ตอบกระชับและครบถ้วนตามที่ถามโดยไม่เพิ่มข้อมูลที่ไม่มีในบริบท"
        
    # STEP 0: Pre-filter Context วิ่งผ่าน Base Model 14B + JSON เพื่อคัดกรอง context ก่อนส่งเข้า LoRA
    print("[INFO] Step 0: Pre-filtering Context (Using Base Model with CoT)")
    prompt0 = f"""<|im_start|>system\nYou are an expert evaluator. Your task is to identify which paragraphs from the context are most relevant to answer the query.\n<|im_end|>\n<|im_start|>user\n[Query]\n{query}\n\n[Context]\n{context}\n\nBriefly analyze the query and extract ONLY the specific paragraph IDs (e.g., P5, P6) that contain information directly relevant and necessary to the query. Filter out irrelevant noise.\n<|im_end|>\n<|im_start|>assistant\n"""
    
    filtered_context = context
    used_refs = []
    pre_filter_success = False
    try:
        pre_filter_output = await llm_client.agenerate_structured(prompt0, PreFilterExtraction, use_lora=False)
        if pre_filter_output:
            pre_filter_success = True
            raw_relevant = getattr(pre_filter_output, 'relevant_refs', []) or []
            
            found_relevant = set()
            for r in raw_relevant:
                r_str = str(r).strip().upper()
                # เผื่อโมเดลตอบมาแค่ตัวเลข 1, 2 แทนที่จะเป็น P1, P2
                match = re.search(r'\d+', r_str)
                if match:
                    found_relevant.add(f"P{match.group()}")
                    
            used_refs = sorted(list(found_relevant), key=lambda x: int(x[1:]))
            
            # Rebuild filtered context เสมอแม้ว่าจะได้ 0 ref ก็ตาม
            lines = context.split('\n')
            filtered_lines = []
            for line in lines:
                match = re.search(r'\[(P\d+)\]:', line)
                if match and match.group(1) in found_relevant:
                    filtered_lines.append(line)
            
            if filtered_lines:
                filtered_context = '\n'.join(filtered_lines)
            else:
                filtered_context = "ไม่พบข้อมูลที่เกี่ยวข้อง"
                used_refs = []
    except Exception as e:
        print(f"\n{'='*50}\n[CRITICAL ERROR] Step 0 (Pre-filter) Failed: {e}\n{'='*50}\n")
        filtered_context = context
        pre_filter_success = False
    print("[DEBUG] pre_filter_output : ",pre_filter_output)
    # STEP 1: วิ่งผ่าน LoRA เพื่อเอาภาษาไทยที่สวยงาม (SS-Score)
    print("[INFO] Step 1: Generating Abstractive Summary (Using LoRA)")
    prompt1 = f"""<|im_start|>system\n{system_instruction}<|im_end|>\n<|im_start|>user\nคำถาม: {query}\n\nบริบทจากเอกสาร:\n{filtered_context}<|im_end|>\n<|im_start|>assistant\n"""
    
    try:
        abstractive = await llm_client.agenerate_text(prompt1, use_lora=True)
        
        def is_invalid(text):
            if not text: return True
            return len(str(text).strip(" .-\n\t\r")) == 0

        if is_invalid(abstractive):
            abstractive = re.sub(r'\[P\d+\]:\s*', '', filtered_context).replace('\n', ' ').strip()
        else:
            thai_to_arabic = str.maketrans('๐๑๒๓๔๕๖๗๘๙', '0123456789')
            abstractive = str(abstractive).translate(thai_to_arabic)
            abstractive = clean_format(abstractive)
            
    except Exception as e:
        print(f"[ERROR] Step 1 (LoRA) Failed: {e}")
        abstractive = re.sub(r'\[P\d+\]:\s*', '', filtered_context).replace('\n', ' ').strip()
        abstractive = clean_format(abstractive)

    # คลีน refs เผื่อมีขยะติดมา
    clean_used_refs = []
    for ref in used_refs:
        if re.match(r'^P\d+$', str(ref).strip()):
            clean_used_refs.append(str(ref).strip())
            
    # Fallback ไปใช้ ref ทั้งหมด เฉพาะกรณีที่ Step 0 Error (pre_filter_success = False) เท่านั้น
    if not pre_filter_success:
        print(f"\n[WARNING] Fallback triggered! Pre-filter failed, using all {len(retriever_refs)} original refs.\n")
        clean_used_refs = retriever_refs
        
    return {
        "abstractive": abstractive,
        "used_refs": clean_used_refs
    }
