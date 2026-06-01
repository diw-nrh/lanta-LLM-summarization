import json
import uuid
import os
from typing import Type
from pydantic import BaseModel
from vllm.engine.async_llm_engine import AsyncLLMEngine
from vllm.engine.arg_utils import AsyncEngineArgs
#from vllm import SamplingParams
from vllm.sampling_params import SamplingParams, GuidedDecodingParams

class LLMClient:
    def __init__(self):
        print("Initializing vLLM AsyncEngine (In-Process)...")
        try:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            model_path = os.path.abspath(os.path.join(current_dir, "../../models/Qwen3-8B")) 
            
            engine_args = AsyncEngineArgs(
                model=model_path,
                trust_remote_code=True,
                max_model_len=32768,
                tensor_parallel_size=1,
                gpu_memory_utilization=0.8, # ✅ 1. ต้องเปลี่ยนเป็น 0.9 ครับ (เพราะโมเดล 14B ใหญ่มาก)
                enforce_eager=True          # ✅ 2. ต้องเติมบรรทัดนี้ เพื่อปิด CUDA Graph และเอา VRAM คืนมาครับ
            )
            self.engine = AsyncLLMEngine.from_engine_args(engine_args)
            print("--- 🚀 Local vLLM Engine Initialized! ---")
        except Exception as e:
            raise RuntimeError(f"Failed to initialize vLLM Engine: {e}")

    async def agenerate_structured(self, prompt: str, schema: Type[BaseModel], temperature: float = 0.1) -> BaseModel:
        schema_json = schema.model_json_schema()
        
        # เปิดใช้งาน guided_json บังคับโครงสร้าง
        sampling_params = SamplingParams(
            temperature=temperature,
            max_tokens=8192,
            guided_decoding=GuidedDecodingParams(json=json.dumps(schema_json))
        )
        
        request_id = str(uuid.uuid4())
        response_text = ""
        try:
            final_output = None
            async for request_output in self.engine.generate(prompt, sampling_params, request_id):
                final_output = request_output
                
            if not final_output or not final_output.outputs:
                print(f"[ERROR] Empty output from vLLM")
                return None
                
            response_text = final_output.outputs[0].text.strip()
            if not response_text:
                print(f"[ERROR] Blank response_text")
                return None
            
            # vLLM guided_json การันตีว่าเป็น JSON แล้ว 
            data = json.loads(response_text)
            return schema.model_validate(data)
                
        except Exception as e:
            print(f"[ERROR] LLM Generation/Parse failed: {e}")
            if response_text:
                print(f"[DEBUG] Raw response that caused the error:\n{repr(response_text)}")
            return None
# Global instance
llm_client = LLMClient()