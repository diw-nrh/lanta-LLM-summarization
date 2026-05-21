from state import main_state
from skills import skill_validator_polish
class AgentCValidator:
    def __init__(self, model_id="/project/zz991000-zdeva/zz991012/my_workspace/models/Qwen2.5-32B-Instruct"):
        self.model_id = model_id

    def validate_node(state: main_state):
        llm = ChatOpenAI(model=self.model_id, temperature=0.2)
        prompt = f"{skill_validator_polish}\n\nQuery: {state.query}\nParagraphs: {state.paras}\n\nAnswer: {state.answer}\n\nIs the answer correct? Answer with 'Yes' or 'No'."
        return 0