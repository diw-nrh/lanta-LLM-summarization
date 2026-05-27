from langgraph.graph import StateGraph, END
from state import GraphState
from nodes import retriever_node, generator_node, validator_node

def route_validator(state: GraphState):
    if state.get("is_valid"):
        return END
        
    if state.get("retry_count", 0) >= 3:
        print("[WARN] MAX RETRIES REACHED. Forcing route to END.")
        return END
    
    route = state.get("route_to", "none")
    if route == "retriever":
        return "agent_a"
    elif route == "generator":
        return "agent_b"
    else:
        return END

# สร้าง Graph
workflow = StateGraph(GraphState)

# เพิ่ม Nodes (ไม่มี formatter แล้ว)
workflow.add_node("agent_a", retriever_node)
workflow.add_node("agent_b", generator_node)
workflow.add_node("agent_c", validator_node)

# กำหนดเส้นทาง
workflow.set_entry_point("agent_a")
workflow.add_edge("agent_a", "agent_b")
workflow.add_edge("agent_b", "agent_c")

# Conditional Edge ออกจาก Agent C โยงเข้า END
workflow.add_conditional_edges(
    "agent_c",
    route_validator,
    {
        "agent_a": "agent_a",
        "agent_b": "agent_b",
        END: END
    }
)

app = workflow.compile()