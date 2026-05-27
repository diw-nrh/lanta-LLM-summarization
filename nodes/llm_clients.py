import json
import uuid
import os
from typing import Type
from pydantic import BaseModel
from vllm.engine.async_llm_engine import AsyncLLMEngine
from vllm.engine.arg_utils import AsyncEngineArgs
from vllm import SamplingParams

class LLMClient:
    def __init__(self):
        print("Initializing vLLM AsyncEngine (In-Process)...")
        try:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            model_path = os.path.abspath(os.path.join(current_dir, "../../models/Qwen2.5-14B-Instruct")) 
            
            engine_args = AsyncEngineArgs(
                model=model_path,
                trust_remote_code=True,
                max_model_len=8192,
                tensor_parallel_size=1,
                gpu_memory_utilization=0.9, # ✅ 1. ต้องเปลี่ยนเป็น 0.9 ครับ (เพราะโมเดล 14B ใหญ่มาก)
                enforce_eager=True          # ✅ 2. ต้องเติมบรรทัดนี้ เพื่อปิด CUDA Graph และเอา VRAM คืนมาครับ
            )
            self.engine = AsyncLLMEngine.from_engine_args(engine_args)
            print("--- 🚀 Local vLLM Engine Initialized! ---")
        except Exception as e:
            raise RuntimeError(f"Failed to initialize vLLM Engine: {e}")

    async def agenerate_structured(self, prompt: str, schema: Type[BaseModel], temperature: float = 0.1) -> BaseModel:
        schema_json = schema.model_json_schema()
        
        # ❌ ลบ guided_params ออก ใช้แค่ SamplingParams ธรรมดา
        sampling_params = SamplingParams(
            temperature=temperature,
            max_tokens=2048
        )
        
        request_id = str(uuid.uuid4())
        
        # ✅ บังคับ JSON ด้วย Prompt แทน
        full_prompt = f"{prompt}\n\n# Output Instructions\nYou MUST return ONLY a raw JSON object that strictly adheres to the schema.\nJSON Schema:\n{json.dumps(schema_json, ensure_ascii=False)}\n\nDo not include markdown code blocks. Just the JSON."
        
        try:
            final_output = None
            async for request_output in self.engine.generate(full_prompt, sampling_params, request_id):
                final_output = request_output
                
            response_text = final_output.outputs[0].text.strip()
            
            # ✅ ใช้วิธีดึงข้อมูลระหว่าง { ... } ที่ปลอดภัยที่สุด
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}')
            
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                json_str = response_text[start_idx:end_idx+1]
                data = json.loads(json_str)
                return schema.model_validate(data)
            else:
                data = json.loads(response_text)
                return schema.model_validate(data)
                
        except Exception as e:
            print(f"[ERROR] Async LLM generation failed: {e}")
            return None

# Global instance
llm_client = LLMClient()