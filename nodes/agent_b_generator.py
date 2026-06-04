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

class RefExtraction(BaseModel):
    used_refs: List[str] = Field(description="List ONLY the specific paragraph IDs (e.g., P5, P6) that contain the exact answers used in the summary. Be extremely strict and minimalistic.")

async def generator_node(state: Dict[str, Any]) -> Dict[str, Any]:
    print("--- RUNNING AGENT B: TWO-STEP HYBRID PIPELINE ---")
    query = state.get("query", "")
    context = state.get("context", "")
    retriever_refs = state.get("refs", [])
    
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
        
    # STEP 1: วิ่งผ่าน LoRA เพื่อเอาภาษาไทยที่สวยงาม (SS-Score)
    print("[INFO] Step 1: Generating Abstractive Summary (Using LoRA)")
    prompt1 = f"""<|im_start|>system\n{system_instruction}<|im_end|>\n<|im_start|>user\nคำถาม: {query}\n\nบริบทจากเอกสาร:\n{context}<|im_end|>\n<|im_start|>assistant\n"""
    
    try:
        abstractive = await llm_client.agenerate_text(prompt1, use_lora=True)
        
        def is_invalid(text):
            if not text: return True
            return len(str(text).strip(" .-\n\t\r")) == 0

        if is_invalid(abstractive):
            abstractive = re.sub(r'\[P\d+\]:\s*', '', context).replace('\n', ' ').strip()
        else:
            thai_to_arabic = str.maketrans('๐๑๒๓๔๕๖๗๘๙', '0123456789')
            abstractive = str(abstractive).translate(thai_to_arabic)
            abstractive = clean_format(abstractive)
            
    except Exception as e:
        print(f"[ERROR] Step 1 (LoRA) Failed: {e}")
        abstractive = re.sub(r'\[P\d+\]:\s*', '', context).replace('\n', ' ').strip()
        abstractive = clean_format(abstractive)

    # STEP 2: วิ่งผ่าน Base Model 14B + JSON เพื่อสกัด Refs (IoU)
    print("[INFO] Step 2: Extracting References (Using Base Model + JSON)")
    prompt2 = f"""<|im_start|>system\nYou are an expert evaluator. Your task is to identify which paragraphs from the context were used to generate the summary.\n<|im_end|>\n<|im_start|>user\n[Context]\n{context}\n\n[Summary]\n{abstractive}\n\nExtract ONLY the specific paragraph IDs (e.g., P5, P6) that contain the direct answers used in the summary. Be extremely strict and minimalistic.\n<|im_end|>\n<|im_start|>assistant\n"""
    
    used_refs = []
    try:
        ref_output = await llm_client.agenerate_structured(prompt2, RefExtraction, use_lora=False)
        if ref_output:
            raw_refs = getattr(ref_output, 'used_refs', []) or []
            found_refs = set(re.findall(r'P\d+', str(raw_refs)))
            used_refs = sorted(list(found_refs), key=lambda x: int(x[1:]))
    except Exception as e:
        print(f"[ERROR] Step 2 (Base Model Ref Extraction) Failed: {e}")
        
    # คลีน refs เผื่อมีขยะติดมา
    clean_used_refs = []
    for ref in used_refs:
        if re.match(r'^P\d+$', str(ref).strip()):
            clean_used_refs.append(str(ref).strip())
    if not clean_used_refs:
        clean_used_refs = retriever_refs
        
    return {
        "abstractive": abstractive,
        "used_refs": clean_used_refs
    }