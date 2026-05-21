from langgraph.graph import StateGraph, END
from state import GraphState
from nodes import retriever_node, generator_node, validator_node, formatter_node

def route_validator(state: GraphState):
    """
    ฟังก์ชันกำหนดเส้นทางตามผลของ Validator
    """
    if state.get("is_valid"):
        return "formatter"
        
    # ป้องกัน Infinite Loop (ถ้า Retry เต็ม 3 รอบแล้ว ให้บังคับไป formatter เลย)
    # เราตั้งเป็น >= 3 เพื่อให้รอบที่ 3 (retry_count=2) ได้วิ่งกลับไปหา Agent A (Brute Force Mode)
    if state.get("retry_count", 0) >= 3:
        print("[WARN] MAX RETRIES REACHED. Forcing route to formatter.")
        return "formatter"
    
    # ถ้าไม่ผ่าน และยังไม่เกินโควต้า ให้ดูว่าจะ route ไปไหน
    route = state.get("route_to", "none")
    if route == "retriever":
        return "agent_a"
    elif route == "generator":
        return "agent_b"
    else:
        return "formatter" # fallback ถ้า retry เต็ม หรือ route_to = none

# สร้าง Graph
workflow = StateGraph(GraphState)

# เพิ่ม Nodes
workflow.add_node("agent_a", retriever_node)
workflow.add_node("agent_b", generator_node)
workflow.add_node("agent_c", validator_node)
workflow.add_node("formatter", formatter_node)

# กำหนดเส้นทาง (Edges)
workflow.set_entry_point("agent_a")
workflow.add_edge("agent_a", "agent_b")
workflow.add_edge("agent_b", "agent_c")

# Conditional Edge ออกจาก Agent C
workflow.add_conditional_edges(
    "agent_c",
    route_validator,
    {
        "agent_a": "agent_a",
        "agent_b": "agent_b",
        "formatter": "formatter"
    }
)

workflow.add_edge("formatter", END)

# Compile เป็น App
app = workflow.compile()
