from state import main_state
from langchain_openai import ChatOpenAI
from skills import skill_generator
class AgentBGenerator:
    def __init__(self):
        self.model_id = "/project/zz991000-zdeva/zz991012/my_workspace/models/Qwen2.5-32B-Instruct"
        
    def generate_node(state: main_state,self):
        #ใช้ query, paras, suggested_answer เพื่อ generate answer
        llm = ChatOpenAI(model=self.model_id, temperature=0.2)
        prompt = f"{skill_generator}\n\nQuery: {state.query}\nParagraphs: {state.paras}\n\nSuggested Answer: {state.suggested_answer}"
        return 0