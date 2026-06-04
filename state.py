from typing import TypedDict, List

class GraphState(TypedDict, total=False):
    # Input fields (required)
    query_id: str
    doc_id: str
    query: str
    
    # State จาก Agent A
    context: str
    refs: List[str]
    
    # State จาก Agent B
    abstractive: str
    used_refs: List[str]