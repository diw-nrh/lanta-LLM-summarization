from typing import Dict, Any, List
from pydantic import BaseModel, Field
import os
import re
from .llm_clients import llm_client

def clean_format(text: str) -> str:
    if not text: return ""
    text = re.sub(r'^(สรุปคือ|คำตอบคือ|สรุปได้ว่า)\s*', '', text)
    text = re.sub(r'(ครับ|ค่ะ)$', '', text).strip()
    return text

class AgentBOutput(BaseModel):
    reasoning: str = Field(description="วิเคราะห์สั้นๆ ว่าจะใช้พารากราฟไหนบ้าง")
    used_refs: List[str] = Field(description="รหัสพารากราฟที่ใช้ เช่น P5, P6")
    abstractive: str = Field(description="สรุปเนื้อหาจากพารากราฟที่เลือก ตอบกระชับและครบถ้วนตามที่ถามโดยไม่เพิ่มข้อมูลที่ไม่มีในบริบท")

async def generator_node(state: Dict[str, Any]) -> Dict[str, Any]:
    print("--- RUNNING AGENT B: SINGLE-PASS RL ARCHITECTURE (JSON SCHEMA) ---")
    query = state.get("query", "")
    context = state.get("context", "")
    retriever_refs = state.get("refs", [])
    
    if context == "ไม่พบคำตอบ":
        return {
            "abstractive": "ไม่พบคำตอบ",
            "used_refs": retriever_refs
        }

    # SYSTEM PROMPT FOR RL SINGLE-PASS
    system_instruction = (
        "คุณคือผู้ช่วยที่เชี่ยวชาญในการสรุปเนื้อหาจากเอกสาร "
        "อ่านคำถามและบริบท จากนั้นให้คิดวิเคราะห์แล้วตอบในรูปแบบ JSON object ดังนี้:\n"
        "{\n"
        '  "reasoning": "วิเคราะห์ว่าต้องใช้พารากราฟไหน",\n'
        '  "used_refs": ["P1", "P2"],\n'
        '  "abstractive": "พิมพ์สรุปเนื้อหาตรงนี้"\n'
        "}\n"
        "ตอบกลับเป็น JSON format ที่ถูกต้องเท่านั้น"
    )

    # Clean the context
    clean_context_lines = []
    for line in context.split('\n'):
        line_clean = re.sub(r'^\[(P\d+)\]:\s*', r'\1: ', line)
        clean_context_lines.append(line_clean)
    train_format_context = '\n'.join(clean_context_lines)

    prompt = f"<|im_start|>system\n{system_instruction}<|im_end|>\n<|im_start|>user\nคำถาม: {query}\n\nบริบทจากเอกสาร:\n{train_format_context}<|im_end|>\n<|im_start|>assistant\n"
    
    abstractive = ""
    used_refs = []
    
    try:
        print("[INFO] Generating Single-Pass Summary & Used Refs (Using RL-tuned LoRA with JSON Schema)")
        
        # ใช้ agenerate_structured เพื่อบังคับให้ได้ JSON Schema ที่ถูกต้อง 100%
        structured_output = await llm_client.agenerate_structured(prompt, AgentBOutput, use_lora=True)
        
        if structured_output:
            # Process Abstractive
            extracted_abstractive = getattr(structured_output, 'abstractive', "")
            thai_to_arabic = str.maketrans('๐๑๒๓๔๕๖๗๘๙', '0123456789')
            abstractive = str(extracted_abstractive).translate(thai_to_arabic)
            abstractive = clean_format(abstractive)
                
            # Process Refs
            raw_refs = getattr(structured_output, 'used_refs', []) or []
            for r in raw_refs:
                match = re.search(r'\d+', str(r))
                if match:
                    used_refs.append(f"P{match.group()}")
        else:
            raise ValueError("Structured output is None")
            
    except Exception as e:
        print(f"[ERROR] Agent B (Single-Pass) Failed: {e}")
        abstractive = clean_format(re.sub(r'P\d+:\s*', '', train_format_context).replace('\n', ' ').strip())
        used_refs = retriever_refs

    # Clean duplicate refs and sort
    used_refs = sorted(list(set(used_refs)), key=lambda x: int(x[1:]))
    if not used_refs:
        used_refs = retriever_refs

    return {
        "abstractive": abstractive,
        "used_refs": used_refs
    }