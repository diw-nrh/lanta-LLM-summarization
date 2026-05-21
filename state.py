from typing import TypedDict, List, Optional

class GraphState(TypedDict):
    query_id: str
    doc_id: str
    query: str
    
    # State จาก Agent A
    context: str
    refs: List[str]
    
    # State จาก Agent B
    abstractive: str
    
    # State จาก Agent C
    is_valid: bool
    route_to: str
    feedback: str
    retry_count: int