from typing import Dict, Any, List
from pydantic import BaseModel, Field
import os
from .llm_clients import llm_client

# -------------------------------------------------------------------
# Pydantic Schemas for LLM Structured Output (Agent C)
# -------------------------------------------------------------------
class ValidatorThoughtProcess(BaseModel):
    draft_evaluation: str = Field(description="Evaluate all provided drafts against the context")
    best_draft_selection: str = Field(description="Select the best draft and explain why")
    context_verification: str = Field(description="Verify facts and numbers against context")
    query_alignment: str = Field(description="Check if answer aligns with query")
    language_check: str = Field(description="Check for 100% Thai and formal tone")
    routing_decision: str = Field(description="Decide if it passes or needs routing")

class ValidatorOutput(BaseModel):
    thought_process: ValidatorThoughtProcess
    is_valid: bool = Field(description="True if passes all checks, False otherwise")
    route_to: str = Field(description="'retriever', 'generator', or 'none'")
    feedback: str = Field(description="Specific feedback for the target agent if invalid")
    final_answer: str = Field(description="The polished answer if valid, or best-effort answer if retry limits reached")

# -------------------------------------------------------------------
# LangGraph Node
# -------------------------------------------------------------------
def validator_node(state: Dict[str, Any]) -> Dict[str, Any]:
    print("--- RUNNING AGENT C: VALIDATOR & VOTER ---")
    query = state.get("query", "")
    drafts = state.get("abstractive_drafts", [])
    abstractive = state.get("abstractive", "")
    context = state.get("context", "")
    retry_count = state.get("retry_count", 0)
    
    drafts_str = "\n".join([f"Draft {i+1}:\n{d}" for i, d in enumerate(drafts)]) if drafts else abstractive
    
    skill_file_path = os.path.join("skills", "skill_validator_polish.md")
    system_instruction = ""
    try:
        with open(skill_file_path, "r", encoding="utf-8") as f:
            system_instruction = f.read()
    except Exception as e:
        print(f"[WARN] Could not load skill file: {e}")
        system_instruction = "You are an expert Validator."
        
    prompt = f"""{system_instruction}

==================================================
YOUR CURRENT TASK (Self-Consistency Voting):

[Query]: {query}
[Retry Count]: {retry_count}

[Abstractive Drafts from Agent B]:
{drafts_str}

[Context Grounding from Agent A]:
{context}

INSTRUCTION: Evaluate all drafts. Select the best one, polish it, and return as final_answer.
==================================================
"""
    try:
        val_output = llm_client.generate_structured(prompt, ValidatorOutput)
        if val_output:
            is_valid = val_output.is_valid
            route_to = val_output.route_to.lower()
            feedback = val_output.feedback
            # Override abstractive with final polished version
            state["abstractive"] = val_output.final_answer
            print(f"Agent C Result: valid={is_valid}, route={route_to}")
        else:
            is_valid = True
            route_to = "none"
            feedback = ""
            print("[WARN] Agent C returned None, forcing valid.")
    except Exception as e:
        print(f"[ERROR] Agent C Failed: {e}")
        is_valid = True
        route_to = "none"
        feedback = ""
        
    return {
        "is_valid": is_valid,
        "route_to": route_to,
        "feedback": feedback,
        "abstractive": state["abstractive"],
        "retry_count": retry_count + 1 if not is_valid else retry_count
    }