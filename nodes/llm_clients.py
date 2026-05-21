from typing import Type
from pydantic import BaseModel
import json
import re

# =================================================================
# ⚙️ CONFIGURATION: ตั้งค่าโหมดการรันโมเดลตรงนี้
# =================================================================
# [True]  = รันโหมดจำลอง (Mock) ใช้สำหรับเทส Data Flow บนคอมธรรมดา
# [False] = รันโมเดลจริง (LANTA) ใช้ตอนประกวด ต้องมี GPU และลง transformers
USE_MOCK = True  
# =================================================================

class LLMClient:
    def __init__(self):
        self.use_mock = USE_MOCK
        if self.use_mock:
            print("Initializing Mock LLM Client (No GPU required)...")
            self.pipe = None
        else:
            print("Initializing Real HuggingFace LLM Client on LANTA...")
            try:
                import torch
                from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline
                
                # สามารถเปลี่ยนชื่อรุ่นโมเดลตรงนี้ได้เลย (เช่น ใช้รุ่น 14B หรือ 32B ถ้า VRAM พอ)
                model_id = "Qwen/Qwen2.5-7B-Instruct"
                print(f"Loading {model_id} with device_map='auto'...")
                
                self.tokenizer = AutoTokenizer.from_pretrained(model_id)
                
                # โหลดโมเดลแบบกระจายลง GPU อัตโนมัติ (device_map="auto") 
                # และใช้ bfloat16 เพื่อประหยัด VRAM และเร็วขึ้น
                self.model = AutoModelForCausalLM.from_pretrained(
                    model_id,
                    device_map="auto",
                    torch_dtype=torch.bfloat16,
                )
                
                self.pipe = pipeline(
                    "text-generation",
                    model=self.model,
                    tokenizer=self.tokenizer,
                    max_new_tokens=1024,
                )
                print("--- 🚀 Model Loaded Successfully! ---")
            except ImportError as e:
                raise RuntimeError(f"Missing required libraries: {e}. Please run: pip install torch transformers accelerate")
            except Exception as e:
                raise RuntimeError(f"Failed to load HuggingFace Model: {e}")
        
    def generate(self, prompt: str) -> str:
        """คืนค่า text ธรรมดา สำหรับ Agent B"""
        if self.use_mock:
            print("[WARN] Running in MOCK Mode. Returning mock text.")
            return "Mock abstractive answer from generator."
        
        if not self.pipe:
            raise RuntimeError("Pipeline is not loaded but USE_MOCK is False!")
            
        # ตัวอย่างการเรียกใช้งาน:
        # outputs = self.pipe(prompt, max_new_tokens=512, return_full_text=False)
        # return outputs[0]["generated_text"].strip()
        return "Mock response from generate"
        
    def generate_structured(self, prompt: str, schema: Type[BaseModel]) -> BaseModel:
        """
        คืนค่าเป็น Pydantic Object สำหรับ Agent A และ Agent C
        เนื่องจาก HF pipelines โลคัลปกติไม่รองรับ Structured Output 100% 
        เราจึงต้องบังคับ Prompt ให้ตอบเป็น JSON แล้วเอามา parse เข้า Pydantic
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
        
        if self.use_mock:
            print(f"[WARN] Running in MOCK Mode. Returning mock Pydantic object for {schema.__name__}.")
            # ส่งคืน Mock Object ชั่วคราวเพื่อให้รันทดสอบได้
            if schema.__name__ == "RankerOutput":
                return schema.model_validate({
                    "thought_process": {"query_analysis": "test", "context_understanding": "test", "key_findings": "test", "self_correction": "test", "final_reasoning": "test"},
                    "contiguous_blocks": [],
                    "paragraph_decisions": [],
                    "selected_refs": ["P1"],
                    "selected_context": "Mock selected context."
                })
            elif schema.__name__ == "EnsembleOutput":
                return schema.model_validate({
                    "thought_process": "Mock ensemble thought",
                    "weights": {"e5_large_weight": 0.3, "bge_m3_weight": 0.5, "wangchanberta_weight": 0.2}
                })
            elif schema.__name__ == "GeneratorOutput":
                return schema.model_validate({
                    "thought_process": {"context_analysis": "x", "query_alignment": "x", "key_extraction": "x", "drafting_strategy": "x", "draft_content": "x", "self_correction_1": "x", "final_polish": "x", "language_check": "x"},
                    "abstractive_drafts": [
                        "Draft 1: ข้อสรุปการประชุม...",
                        "Draft 2: สรุปมติที่ประชุม...",
                        "Draft 3: ผลการพิจารณา..."
                    ],
                    "abstractive": "Mock final generated answer."
                })
            elif schema.__name__ == "ValidatorOutput":
                return schema.model_validate({
                    "thought_process": {"draft_evaluation": "x", "best_draft_selection": "x", "context_verification": "x", "query_alignment": "x", "language_check": "x", "routing_decision": "x"},
                    "is_valid": True,
                    "route_to": "none",
                    "feedback": "",
                    "final_answer": "Mock Polished Answer from Validator"
                })
            else:
                return None

        if not self.pipe:
            raise RuntimeError(f"Pipeline is not loaded but USE_MOCK is False! Cannot generate for {schema.__name__}")

        # ถ้ามีโมเดลจริง ให้รันแล้ว Parse JSON
        response_text = ""
        try:
            outputs = self.pipe(full_prompt, max_new_tokens=1024, return_full_text=False, do_sample=False)
            response_text = outputs[0]["generated_text"].strip()
        except Exception as e:
            print(f"[ERROR] LLM generation failed: {e}")
            return None

        try:
            # หา JSON block ใน response
            match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                return schema.model_validate(data)
            else:
                print(f"[WARN] No JSON found in LLM response for {schema.__name__}")
                return None
        except Exception as e:
            print(f"[ERROR] Failed to parse JSON for {schema.__name__}: {e}")
            return None

# Global instance
llm_client = LLMClient()
