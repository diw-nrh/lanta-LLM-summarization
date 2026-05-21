from typing import Dict, Any
from .llm_clients import llm_client

def generator_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Agent B: Generator
    รับ query + context + refs → สร้าง prompt → เรียก LLM → คืน abstractive summary
    """
    print("--- RUNNING AGENT B: GENERATOR ---")
    query = state.get("query")
    context = state.get("context")
    feedback = state.get("feedback")
    
    # อ่าน Prompt skill_generator.md 
    # สร้าง Payload ให้ LLM
    
    # Mock data for skeleton
    abstractive = "สรุปคำตอบภาษาไทยทางการ"
    
    return {
        "abstractive": abstractive
    }