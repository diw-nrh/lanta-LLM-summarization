from typing import Dict, Any, List
from pydantic import BaseModel, Field
import os
from .llm_clients import llm_client

class GeneratorOutput(BaseModel):
    analysis: str = Field(description="Analyze the query and context")
    draft_content: str = Field(description="Initial detailed answer based on context")
    self_correction: str = Field(description="Check for hallucinations and completeness")
    final_polish: str = Field(description="Ensure tone is formal and correct")
    abstractive: str = Field(description="The primary selected answer (for fallback)")

async def generator_node(state: Dict[str, Any]) -> Dict[str, Any]:
    print("--- RUNNING AGENT B: GENERATOR ---")
    query = state.get("query", "")
    context = state.get("context", "")
    
    if context == "ไม่พบคำตอบ":
        return {
            "abstractive": "ไม่พบคำตอบ"
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
    print("[Debug] : ", prompt)
    try:
        gen_output = await llm_client.agenerate_structured(prompt, GeneratorOutput)
        if gen_output:
            abstractive = gen_output.abstractive
            thai_to_arabic = str.maketrans('๐๑๒๓๔๕๖๗๘๙', '0123456789')
            abstractive = abstractive.translate(thai_to_arabic)
        else:
            abstractive = "Fallback abstractive answer due to parse error."
    except Exception as e:
        print(f"[ERROR] Agent B Failed: {e}")
        abstractive = "Error during generation."
        
    return {
        "abstractive": abstractive
    }