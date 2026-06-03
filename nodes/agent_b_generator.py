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

class GeneratorOutput(BaseModel):
    analysis: str = Field(description="Analyze the query. Scan the context for the core facts.")
    pattern_matching: str = Field(description="Identify the query type to select the correct formatting pattern (Entity, Time/Location, List, Summary, or Resolution).")
    draft_content: str = Field(description="Extract the facts.")
    refinement: str = Field(description="Rewrite the facts into a full, formal Thai sentence. You MUST echo the subject of the query to form a complete sentence.")
    numeral_and_entity_check: str = Field(description="Verify that ALL numbers are converted to Arabic numerals. Verify that NO personal names are masked; use the exact names.")
    abstractive: str = Field(description="Provide the final formatted answer.")
    used_refs: List[str] = Field(description="List all paragraph IDs used.")

async def generator_node(state: Dict[str, Any]) -> Dict[str, Any]:
    print("--- RUNNING AGENT B: GENERATOR ---")
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
    system_instruction = ""
    try:
        with open(skill_file_path, "r", encoding="utf-8") as f:
            system_instruction = f.read()
    except Exception as e:
        system_instruction = "You are an expert Generator."
        
    prompt = f"""{system_instruction}

==================================================
YOUR CURRENT TASK:

[Query]: {query}

[Context from Retriever]:
{context}
==================================================
"""
    try:
        gen_output = await llm_client.agenerate_structured(prompt, GeneratorOutput)
        if gen_output:
            abstractive = gen_output.abstractive
            if not abstractive or abstractive.strip() == "":
                # print("[WARN] abstractive is empty! Falling back to refinement or draft_content (มโน mode)...")
                abstractive = gen_output.refinement if gen_output.refinement else gen_output.draft_content
                if not abstractive:
                    # ไม้ตายสุดท้าย: ดึง Text ดิบๆ จากที่ Reranker หามาได้ ส่งไปเลย!
                    abstractive = re.sub(r'\[P\d+\]:\s*', '', context).replace('\n', ' ').strip()
            
            used_refs = gen_output.used_refs or []
            if not used_refs:
                used_refs = retriever_refs
                
            thai_to_arabic = str.maketrans('๐๑๒๓๔๕๖๗๘๙', '0123456789')
            abstractive = str(abstractive).translate(thai_to_arabic)
            abstractive = clean_format(abstractive)
        else:
            abstractive = "ไม่พบข้อมูลที่เพียงพอสำหรับสรุปคำตอบ"
            used_refs = retriever_refs
        # print("[😍DEBUG] gen_output",gen_output)
    except Exception as e:
        print(f"[ERROR] Agent B Failed: {e}")
        abstractive = "Error during generation."
        used_refs = []
        
    return {
        "abstractive": abstractive,
        "used_refs": used_refs
    }