from typing import Type
from pydantic import BaseModel

class LLMClient:
    def __init__(self):
        # จัดการโหลด HuggingFace Local Model (SeaLLM / Typhoon / Qwen) บน Lanta
        pass
        
    def generate(self, prompt: str) -> str:
        """คืนค่า text ธรรมดา"""
        pass
        
    def generate_structured(self, prompt: str, schema: Type[BaseModel]) -> BaseModel:
        """คืนค่าเป็น Pydantic Object ตาม Schema (จำลอง with_structured_output)"""
        pass

# Global instance ให้เรียกใช้
llm_client = LLMClient()
