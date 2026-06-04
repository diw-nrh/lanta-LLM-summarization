from langgraph.graph import StateGraph, END
from state import GraphState
from run.nodes.agent_a_retriever import retriever_node
from run.nodes.agent_b_generator import generator_node

# สร้าง Graph
workflow = StateGraph(GraphState)

# เพิ่ม Nodes
workflow.add_node("agent_a", retriever_node)
workflow.add_node("agent_b", generator_node)

# กำหนดเส้นทาง
workflow.set_entry_point("agent_a")
workflow.add_edge("agent_a", "agent_b")
workflow.add_edge("agent_b", END)

app = workflow.compile()