import json
import uuid
import os
from typing import Type
from pydantic import BaseModel
from vllm.engine.async_llm_engine import AsyncLLMEngine
from vllm.engine.arg_utils import AsyncEngineArgs
from vllm.sampling_params import SamplingParams, GuidedDecodingParams

class LLMClient:
    def __init__(self):
        print("Initializing vLLM AsyncEngine (In-Process)...")
        try:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            base_model_path = os.path.abspath(os.path.join(current_dir, "../../models/Qwen3-8B")) 
            
            engine_args = AsyncEngineArgs(
                model=base_model_path,
                trust_remote_code=True,
                max_model_len=32768,
                tensor_parallel_size=1,
                gpu_memory_utilization=0.8,
                enforce_eager=True
            )
            self.engine = AsyncLLMEngine.from_engine_args(engine_args)
            print("--- 🚀 Local vLLM Engine Initialized! ---")
        except Exception as e:
            raise RuntimeError(f"Failed to initialize vLLM Engine: {e}")

    async def agenerate_structured(self, prompt: str, schema: Type[BaseModel], temperature: float = 0.1) -> BaseModel:
        schema_json = schema.model_json_schema()
        
        # เปิดใช้งาน guided_json บังคับโครงสร้าง
        sampling_params = SamplingParams(
            temperature=0.3,          # 🌟 ปรับขึ้นจาก 0.1 เป็น 0.3 - 0.4
            top_p=0.85,               # 🌟 เพิ่ม Top-p เพื่อตัด Token หางแถวที่ทำให้หลงทาง
            top_k=40,
            max_tokens=5120, # ลด Max Tokens เป็น 1024 เพื่อกันปัญหา OOM (Exit 137)
            stop=["<|im_end|>", "<|endoftext|>"], 
            repetition_penalty=1.00,  # 🌟 ปิด Penalty กันชื่อเฉพาะพัง
            presence_penalty=0.00,    # 🌟 เน้นใช้ค่านี้แทน เพื่อสะกิดให้โมเดลเปลี่ยนไปพูดเรื่องใหม่เมื่อจบประโยค
            frequency_penalty=0.00, # เปิดไว้อ่อนๆ กันลูป
            guided_decoding=GuidedDecodingParams(
                json=json.dumps(schema_json),
                backend="xgrammar"
            )
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

    async def agenerate_text(self, prompt: str, temperature: float = 0.1) -> str:
        sampling_params = SamplingParams(
            temperature=temperature,    # 🌟 ปรับขึ้นจาก 0.1 เป็น 0.3 - 0.4
            top_p=0.85,               # 🌟 เพิ่ม Top-p เพื่อตัด Token หางแถวที่ทำให้หลงทาง
            top_k=40,
            max_tokens=1024,
            stop=["<|im_end|>", "<|endoftext|>"], 
            repetition_penalty=1.00,  # 🌟 ปิด Penalty กันชื่อเฉพาะพัง
            presence_penalty=0.00,    # 🌟 เน้นใช้ค่านี้แทน เพื่อสะกิดให้โมเดลเปลี่ยนไปพูดเรื่องใหม่เมื่อจบประโยค
            frequency_penalty=0.00, # เปิดไว้อ่อนๆ กันลูป
        )
        
        request_id = str(uuid.uuid4())
        try:
            final_output = None
            async for request_output in self.engine.generate(prompt, sampling_params, request_id):
                final_output = request_output
                
            if not final_output or not final_output.outputs:
                return ""
                
            return final_output.outputs[0].text.strip()
        except Exception as e:
            print(f"[ERROR] LLM Generation failed: {e}")
            return ""

# --- ใส่ไว้ล่างสุดของไฟล์ llm_clients.py ---
#_llm_client_instance = None

#def get_llm_client():
 #  global _llm_client_instance
  # if _llm_client_instance is None:
   #    _llm_client_instance = LLMClient()
    #return _llm_client_instance
llm_client=LLMClient()