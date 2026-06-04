"""
train_rl.py
---------------
Train Agent B (Single-Pass) using GRPO with actual competition metrics as the Reward Function.
"""

import os
import json
import argparse
from dataclasses import dataclass
import re

import pandas as pd
import torch
import torch.nn.functional as F
from datasets import Dataset
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import LoraConfig, get_peft_model, TaskType
from trl import GRPOTrainer, GRPOConfig

from sentence_transformers import SentenceTransformer
from pythainlp.tokenize import word_tokenize
from rouge_score import rouge_scorer
from rouge_score.tokenizers import Tokenizer

# ═══════════════════════════════════════════════════════
# 1. EVALUATION UTILS & REWARD FUNCTIONS
# ═══════════════════════════════════════════════════════
device = "cuda" if torch.cuda.is_available() else "cpu"

def tokenize_thai(text):
    if not isinstance(text, str) or text.strip() == "":
        return ""
    tokens = word_tokenize(text, engine="newmm", keep_whitespace=False)
    return " ".join(tokens)

class ThaiSpaceTokenizer(Tokenizer):
    def tokenize(self, text):
        return text.split(" ")

def calculate_iou(list_pred, list_sol):
    set_pred = set(list_pred) if isinstance(list_pred, list) else set()
    set_sol = set(list_sol) if isinstance(list_sol, list) else set()
    if not set_sol: return 0.0
    if not set_pred: return 0.0
    return len(set_pred.intersection(set_sol)) / len(set_pred.union(set_sol))

# Load bge-m3 globally to avoid reloading inside the reward function
embedding_model = None

def get_embedding_model(model_path):
    global embedding_model
    if embedding_model is None:
        embedding_model = SentenceTransformer(model_path)
    return embedding_model

def competition_reward_func(completions, sol_refs, sol_abstractive, **kwargs):
    """
    GRPO Reward function that uses the exact competition metrics!
    """
    import json
    
    pred_abstractives = []
    pred_refs_list = []
    format_rewards = []
    
    for comp in completions:
        # TRL GRPO completions usually come as a list of strings
        text = comp[0]["content"] if isinstance(comp, list) else str(comp)
        text = text.strip()
        
        # Try to parse JSON
        try:
            # If model wraps in ```json ... ```, strip it
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
                
            parsed = json.loads(text)
            
            # Allow keys to be flexible if model makes minor mistakes during RL
            a = parsed.get("abstractive", "")
            r_list = parsed.get("used_refs", [])
            
            if isinstance(r_list, str):
                r_list = [r.strip() for r in r_list.split(",") if r.strip()]
            
            pred_abstractives.append(str(a))
            pred_refs_list.append(r_list)
            
            # Format bonus
            format_rewards.append(0.2)
        except Exception:
            # Penalty for invalid JSON during RL
            pred_abstractives.append("")
            pred_refs_list.append([])
            format_rewards.append(-0.5)

    # 2. Calculate IoU
    iou_scores = [calculate_iou(p, s) for p, s in zip(pred_refs_list, sol_refs)]
    
    # 3. Calculate Rouge-L
    scorer = rouge_scorer.RougeScorer(['rougeL'], use_stemmer=False, tokenizer=ThaiSpaceTokenizer())
    sol_toks = [tokenize_thai(s) for s in sol_abstractive]
    pred_toks = [tokenize_thai(p) for p in pred_abstractives]
    
    rouge_scores = []
    for g, p in zip(sol_toks, pred_toks):
        if not p: # Empty prediction
            rouge_scores.append(0.0)
        else:
            rouge_scores.append(scorer.score(g, p)['rougeL'].fmeasure)
            
    # 4. Calculate SS-score (bge-m3)
    bge = get_embedding_model(kwargs.get('bge_model_path', '/lustrefs/disk/project/zz991000-zdeva/zz991012/my_workspace/submission/models/bge-m3'))
    
    # Handle empty predictions so bge doesn't crash
    safe_pred_abstractives = [p if p else "ไม่มีข้อความ" for p in pred_abstractives]
    
    texts = sol_abstractive + safe_pred_abstractives
    embeddings = bge.encode(texts, batch_size=32, convert_to_tensor=True, normalize_embeddings=True)
    
    ref_emb = embeddings[0:len(sol_abstractive)]
    pred_emb = embeddings[len(sol_abstractive):]
    
    ss_scores = F.cosine_similarity(pred_emb, ref_emb, dim=1).cpu().numpy().tolist()
    
    # 5. Final Reward Calculation
    final_rewards = []
    wss, wrl, wj = 0.45, 0.35, 0.2
    
    for ss, rl, iou, fmt in zip(ss_scores, rouge_scores, iou_scores, format_rewards):
        score = (wss * ss) + (wrl * rl) + (wj * iou)
        # Final reward = Competition Score + Format Bonus
        final_rewards.append(score + fmt)
        
    return final_rewards


# ═══════════════════════════════════════════════════════
# 2. CONFIG
# ═══════════════════════════════════════════════════════
@dataclass
class RLScriptArgs:
    model_name: str = "/lustrefs/disk/project/zz991000-zdeva/zz991012/my_workspace/submission/models/Qwen3-8B"
    bge_model_path: str = "/lustrefs/disk/project/zz991000-zdeva/zz991012/my_workspace/submission/models/bge-m3"
    train_file: str = "data/train_rl_rag.json"
    
    # LoRA
    lora_r:       int   = 32
    lora_alpha:   int   = 64
    lora_dropout: float = 0.05
    lora_target:  str   = "q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj"

    # RL GRPO
    output_dir:                  str   = "models_rl"
    num_train_epochs:            int   = 1 # RL usually needs fewer epochs
    per_device_train_batch_size: int   = 1
    gradient_accumulation_steps: int   = 8
    learning_rate:               float = 1e-5 # Smaller LR for RL
    max_completion_length:       int   = 1024
    num_generations:             int   = 4 # How many responses to generate per prompt (requires VRAM)

    bf16: bool = True
    seed: int  = 42

# ═══════════════════════════════════════════════════════
# 3. DATASET PREPARATION
# ═══════════════════════════════════════════════════════
SYSTEM_PROMPT = (
    "คุณคือผู้ช่วยที่เชี่ยวชาญในการสรุปเนื้อหาจากเอกสาร "
    "อ่านคำถามและบริบท จากนั้นให้คิดวิเคราะห์แล้วตอบในรูปแบบ JSON object ดังนี้:\n"
    "{\n"
    '  "reasoning": "วิเคราะห์ว่าต้องใช้พารากราฟไหน",\n'
    '  "used_refs": ["P1", "P2"],\n'
    '  "abstractive": "พิมพ์สรุปเนื้อหาตรงนี้"\n'
    "}\n"
    "ตอบกลับเป็น JSON format ที่ถูกต้องเท่านั้น"
)

def build_prompt(query: str, retrieved_context: str) -> str:
    return f"คำถาม: {query}\n\nบริบทจากเอกสาร:\n{retrieved_context}"

def load_rl_dataset(input_path: str):
    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    prompts, sol_refs_list, sol_abs_list = [], [], []
    
    for q in data.get("queries", []):
        query    = q.get("query", "")
        context  = q.get("retrieved_context", "")
        sol_refs = q.get("sol_refs", [])
        answer   = q.get("sol_abstractive", "")
        
        user_msg = build_prompt(query, context)
        
        # TRL expects messages format for prompt
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg}
        ]
        
        prompts.append(messages)
        sol_refs_list.append(sol_refs)
        sol_abs_list.append(answer)

    # Convert to HuggingFace Dataset
    ds = Dataset.from_dict({
        "prompt": prompts,
        "sol_refs": sol_refs_list,
        "sol_abstractive": sol_abs_list
    })
    return ds

# ═══════════════════════════════════════════════════════
# 4. TRAIN
# ═══════════════════════════════════════════════════════
def train(args: RLScriptArgs):
    # Pre-load BGE model so it doesn't load multiple times
    get_embedding_model(args.bge_model_path)
    
    dataset = load_rl_dataset(args.train_file)
    
    target_modules = [m.strip() for m in args.lora_target.split(",")]
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        target_modules=target_modules,
        bias="none",
    )

    # Only pass valid arguments to GRPOConfig according to the new API
    training_args = GRPOConfig(
        output_dir=args.output_dir,
        learning_rate=args.learning_rate,
        per_device_train_batch_size=args.per_device_train_batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        num_train_epochs=args.num_train_epochs,
        bf16=args.bf16,
        max_completion_length=args.max_completion_length,
        num_generations=args.num_generations,
        logging_steps=10,
        save_steps=100,
        seed=args.seed,
    )
    
    # Wrapper for kwargs passing to reward function
    def wrapped_reward(prompts, completions, sol_refs, sol_abstractive, **kwargs):
        return competition_reward_func(completions, sol_refs, sol_abstractive, bge_model_path=args.bge_model_path)
    
    trainer = GRPOTrainer(
        model=args.model_name,
        reward_funcs=[wrapped_reward],
        args=training_args,
        train_dataset=dataset,
        peft_config=lora_config
    )

    print("\n🚀 Start RL Training (GRPO)...")
    trainer.train()

    final_dir = os.path.join(args.output_dir, "final")
    trainer.save_model(final_dir)
    print(f"\n✅ Saved RL Model → {final_dir}")

if __name__ == "__main__":
    args = RLScriptArgs()
    parser = argparse.ArgumentParser()
    for f in args.__dataclass_fields__:
        default = getattr(args, f)
        parser.add_argument(f"--{f}", type=type(default), default=default)
    cli, _ = parser.parse_known_args()
    for f in args.__dataclass_fields__:
        setattr(args, f, getattr(cli, f))
    train(args)
