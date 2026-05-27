from typing import Dict, Any, List
from pydantic import BaseModel, Field
import os
from .llm_clients import llm_client

class GeneratorThoughtProcess(BaseModel):
    analysis: str = Field(description="Analyze the query and context")
    draft_content: str = Field(description="Initial detailed answer based on context")
    self_correction: str = Field(description="Check for hallucinations and completeness")
    final_polish: str = Field(description="Ensure tone is formal and correct")

class GeneratorOutput(BaseModel):
    thought_process: GeneratorThoughtProcess
    abstractive_drafts: List[str] = Field(description="Generate 3 distinct drafts of the abstractive answer in formal Thai")
    abstractive: str = Field(description="The primary selected answer (for fallback)")

async def generator_node(state: Dict[str, Any]) -> Dict[str, Any]:
    print("--- RUNNING AGENT B: GENERATOR ---")
    query = state.get("query", "")
    context = state.get("context", "")
    feedback = state.get("feedback", "")
    
    if context == "ไม่พบคำตอบ":
        return {
            "abstractive_drafts": ["ไม่พบคำตอบ"],
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
        
    feedback_str = f"Feedback from Validator (Retry): {feedback}" if feedback else "No feedback. Initial run."
    
    prompt = f"""{system_instruction}

==================================================
YOUR CURRENT TASK:

[Query]: {query}
[System State]: {feedback_str}

[Context from Retriever]:
{context}
==================================================
"""
    try:
        gen_output = await llm_client.agenerate_structured(prompt, GeneratorOutput)
        if gen_output:
            drafts = gen_output.abstractive_drafts
            abstractive = gen_output.abstractive
        else:
            drafts = ["Fallback draft 1", "Fallback draft 2", "Fallback draft 3"]
            abstractive = "Fallback abstractive answer due to parse error."
    except Exception as e:
        print(f"[ERROR] Agent B Failed: {e}")
        drafts = ["Error draft"]
        abstractive = "Error during generation."
        
    return {
        "abstractive_drafts": drafts,
        "abstractive": abstractive
    }