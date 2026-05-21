from typing import TypedDict, List, Optional

class GraphState(TypedDict, total=False):
    # Input fields (required)
    query_id: str
    doc_id: str
    query: str
    retry_count: int
    
    # State จาก Agent A
    context: str
    refs: List[str]
    
    # State จาก Agent B
    abstractive_drafts: List[str]
    abstractive: str
    
    # State จาก Agent C
    is_valid: bool
    route_to: str
    feedback: str