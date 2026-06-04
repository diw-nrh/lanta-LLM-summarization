from typing import Dict, Any, List
from pydantic import BaseModel, Field
import os
from .llm_clients import llm_client

class ValidatorThoughtProcess(BaseModel):
    # แก้จาก Evaluate drafts เป็น Evaluate the answer
    evaluation: str = Field(description="Evaluate the answer for truthfulness and completeness")
    fact_check: str = Field(description="Verify all facts against the context")
    routing_decision: str = Field(description="Decide if it passes or needs routing")

class ValidatorOutput(BaseModel):
    thought_process: ValidatorThoughtProcess
    is_valid: bool = Field(description="True if passes all checks, False otherwise")
    route_to: str = Field(description="'retriever', 'generator', or 'none'")
    feedback: str = Field(description="Specific feedback for the target agent if invalid")
    final_answer: str = Field(description="The polished answer if valid, or best-effort answer if retry limits reached")

async def validator_node(state: Dict[str, Any]) -> Dict[str, Any]:
    print("--- RUNNING AGENT C: VALIDATOR & POLISHER ---")
    query = state.get("query", "")
    abstractive = state.get("abstractive", "") # รับคำตอบเดียวมาเลย
    context = state.get("context", "")
    retry_count = state.get("retry_count", 0)
    
    if abstractive == "ไม่พบคำตอบ":
        return {
            "is_valid": True,
            "route_to": "none",
            "feedback": "",
            "abstractive": "ไม่พบคำตอบ",
            "retry_count": retry_count
        }
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    skill_file_path = os.path.abspath(os.path.join(current_dir, "..", "skills", "skill_validator_polish.md"))
    
    system_instruction = ""
    try:
        with open(skill_file_path, "r", encoding="utf-8") as f:
            system_instruction = f.read()
    except Exception as e:
        system_instruction = "You are an expert Validator."
        
    prompt = f"""{system_instruction}

==================================================
YOUR CURRENT TASK (Quality Gate & Polishing):

[Query]: {query}
[Retry Count]: {retry_count}

[Answer from Generator (Agent B)]:
{abstractive}

[Context Grounding from Agent A]:
{context}

INSTRUCTION: Evaluate the answer. If it is accurate and complete, polish it and return as final_answer. If it fails, route it back with feedback.
==================================================
"""
    try:
        val_output = await llm_client.agenerate_structured(prompt, ValidatorOutput)
        if val_output:
            is_valid = val_output.is_valid
            route_to = val_output.route_to.lower()
            feedback = val_output.feedback
            state["abstractive"] = val_output.final_answer
        else:
            is_valid = True
            route_to = "none"
            feedback = ""
    except Exception as e:
        print(f"[ERROR] Agent C Failed: {e}")
        is_valid = True
        route_to = "none"
        feedback = ""
        
    return {
        "is_valid": is_valid,
        "route_to": route_to,
        "feedback": feedback,
        "abstractive": state.get("abstractive", ""),
        "retry_count": retry_count + 1 if not is_valid else retry_count
    }