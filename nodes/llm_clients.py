from typing import Type
from pydantic import BaseModel
import json
import re
from openai import OpenAI

class LLMClient:
    def __init__(self):
        print("Initializing OpenAI-compatible LLM Client via Novita API...")
        try:
            self.client = OpenAI(
                api_key="sk_r8iPD9QTonepwvFVEmqTaH4gL8PQVX6UGSGyGhh1-WI", # อย่าลืมเปลี่ยนเป็น API Key จริงของคุณ
                base_url="https://api.novita.ai/openai"
            )
            self.model_name = "deepseek/deepseek-v3.2"
            print(f"--- 🚀 Novita API Client Initialized ({self.model_name})! ---")
        except Exception as e:
            raise RuntimeError(f"Failed to initialize OpenAI Client: {e}. Please run: pip install openai")
        
    def generate(self, prompt: str) -> str:
        """คืนค่า text ธรรมดา สำหรับ Agent B"""
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=8192,
                temperature=0.7
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[ERROR] LLM generation failed: {e}")
            return ""
        
    def generate_structured(self, prompt: str, schema: Type[BaseModel]) -> BaseModel:
        """
        คืนค่าเป็น Pydantic Object
        บังคับ Prompt ให้ตอบเป็น JSON แล้วเอามา parse เข้า Pydantic
        """
        schema_json = schema.model_json_schema()
        schema_str = json.dumps(schema_json, ensure_ascii=False, indent=2)
        
        full_prompt = f"""{prompt}

# Output Instructions
You MUST return ONLY a raw JSON object that strictly adheres to the following JSON Schema. 
Do not include markdown code blocks. Do not include any explanations.

JSON Schema:
{schema_str}
"""
        
        response_text = ""
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that always responds with valid JSON."},
                    {"role": "user", "content": full_prompt}
                ],
                max_tokens=8192,
                temperature=0.1
            )
            response_text = response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[ERROR] LLM generation failed: {e}")
            return None

        try:
            # หา JSON block ใน response (รองรับกรณีมี markdown code block ติดมา)
            match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                return schema.model_validate(data)
            else:
                print(f"[WARN] No JSON found in LLM response for {schema.__name__}")
                return None
        except Exception as e:
            print(f"[ERROR] Failed to parse JSON for {schema.__name__}: {e}")
            print(f"Raw response: {response_text[:200]}...")
            return None

# Global instance
llm_client = LLMClient()
