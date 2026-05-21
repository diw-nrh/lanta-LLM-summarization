from nodes.agent_c_validator_polish import AgentCValidator
from nodes.agent_b_generator import AgentBGenerator
from nodes.agent_a_retriever import AgentARetriever
retriever_node = AgentARetriever()
generator_node = AgentBGenerator(model_id="/project/zz991000-zdeva/zz991012/my_workspace/models/Qwen2.5-32B-Instruct")
validator_node = AgentCValidator(model_id="/project/zz991000-zdeva/zz991012/my_workspace/models/Qwen2.5-32B-Instruct")
