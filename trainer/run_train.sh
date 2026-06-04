#!/bin/bash
# run_train.sh
# ─────────────────────────────────────────────────────────────────────────────
# รัน pipeline ทั้งหมด:  prepare data  →  train  →  inference demo
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── 0. ตรวจสอบ GPU ────────────────────────────────────────────────────────────
echo "════════════════════════════════════════"
echo "  GPU Info"
echo "════════════════════════════════════════"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader
echo ""

# ── 1. ตั้งค่า environment ────────────────────────────────────────────────────
export CUDA_VISIBLE_DEVICES=0,1,2,3          # ใช้ 4 GPU
export TOKENIZERS_PARALLELISM=false          # ป้องกัน deadlock
export NCCL_DEBUG=WARN                       # ลด noise ของ NCCL log
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True  # ป้องกัน frag

# ── 2. Prepare data ───────────────────────────────────────────────────────────
echo "════════════════════════════════════════"
echo "  Step 1/3: Preparing data"
echo "════════════════════════════════════════"
python prepare_data.py
echo ""

# ── 3. Train ──────────────────────────────────────────────────────────────────
echo "════════════════════════════════════════"
echo "  Step 2/3: Training (4 GPUs)"
echo "════════════════════════════════════════"
accelerate launch \
    --config_file accelerate_config.yaml \
    train_lora.py \
    --model_name "Qwen/Qwen3-8B" \
    --train_file  data/train.jsonl \
    --val_file    data/val.jsonl \
    --output_dir  outputs/qwen3-8b-lora-summary \
    --num_train_epochs 3 \
    --per_device_train_batch_size 4 \
    --gradient_accumulation_steps 2 \
    --learning_rate 2e-4 \
    --max_seq_length 4096 \
    --lora_r 64 \
    --lora_alpha 128 \
    --bf16 True \
    --disable_thinking True
echo ""

# ── 4. Inference demo ─────────────────────────────────────────────────────────
echo "════════════════════════════════════════"
echo "  Step 3/3: Inference demo"
echo "════════════════════════════════════════"
python inference.py \
    --adapter_path outputs/qwen3-8b-lora-summary/final \
    --mode demo

echo ""
echo "✅  All done!"
