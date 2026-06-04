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
    analysis: str = Field(description="Analyze the query and scan the context for the core facts.")
    relevance_filter: str = Field(description="Briefly list which paragraphs are relevant to the query.")
    extracted_facts: str = Field(description="Extract the exact sentences from the text that answer the query. Preserve the EXACT original wording as much as possible to maximize accuracy.")
    abstractive: str = Field(description="Combine the extracted facts smoothly. DO NOT rewrite or change the vocabulary. Answer directly using the original phrasing from the context.")
    used_refs: List[str] = Field(description="List EVERY SINGLE paragraph ID (Pxx) that contains the facts used in your answer. If the context spans multiple paragraphs (e.g., P50, P51, P52), you MUST include ALL of them. Do NOT just list one.")


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
            
            def is_invalid(text):
                if not text: return True
                return len(str(text).strip(" .-\n\t\r")) == 0

            if is_invalid(abstractive):
                if not is_invalid(gen_output.extracted_facts):
                    abstractive = gen_output.extracted_facts
                else:
                    abstractive = re.sub(r'\[P\d+\]:\s*', '', context).replace('\n', ' ').strip()
            
            raw_refs = gen_output.used_refs or []
            used_refs = []
            # Extract all P\d+ patterns from whatever the LLM returned
            found_refs = set(re.findall(r'P\d+', str(raw_refs)))
            used_refs = sorted(list(found_refs), key=lambda x: int(x[1:]))
                    
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