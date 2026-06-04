#!/bin/bash
#SBATCH -p gpu
#SBATCH -N 1 -c 16
#SBATCH --gpus-per-node=4
#SBATCH --ntasks-per-node=1                  
#SBATCH -t 12:00:00
#SBATCH -A zz991012
#SBATCH -J qwen_rl_train
#SBATCH --output=log_file/train_log/train_rl_job_%j.out

set -euo pipefail

umask 002

cd /project/zz991000-zdeva/zz991012/my_workspace/submission

mkdir -p models_rl/
mkdir -p log_file/train_log/

export MASTER_PORT=$(expr 10000 + $(echo -n $SLURM_JOBID | tail -c 4))
export WORLD_SIZE=4
export MASTER_ADDR=$(scontrol show hostnames "$SLURM_JOB_NODELIST" | head -n 1)

echo "MASTER_ADDR="$MASTER_ADDR
echo "MASTER_PORT="$MASTER_PORT

module load Miniforge3/25.3.0-3
module load cuda/12.6
module load gcc/11.2.0
export CUDA_HOME=$(dirname $(dirname $(which nvcc)))

conda activate /project/zz991000-zdeva/zz991012/summarize_101  

export CUDA_VISIBLE_DEVICES=0,1,2,3
export TOKENIZERS_PARALLELISM=false
export NCCL_DEBUG=WARN
export NCCL_SOCKET_IFNAME=hsn
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

START=`date`
echo "Job start at" $START

echo "GPU Info"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader

# Step 1: Prepare RL Data using Agent A
echo "=== Step 1: Running Agent A RAG Preparation ==="
#/project/zz991000-zdeva/zz991012/summarize_101/bin/python trainer/prepare_rl_data.py

# Step 2: Train RL (GRPO)
echo "=== Step 2: Starting RL Training ==="
accelerate launch \
    --config_file trainer/accelerate_config.yaml \
    --main_process_port $MASTER_PORT \
    trainer/train_rl.py \
    --model_name "/lustrefs/disk/project/zz991000-zdeva/zz991012/my_workspace/submission/models/Qwen3-8B" \
    --bge_model_path "/lustrefs/disk/project/zz991000-zdeva/zz991012/my_workspace/submission/models/bge-m3" \
    --train_file "data/train_set.json" \
    --output_dir "models_rl/" \
    --num_train_epochs 1 \
    --per_device_train_batch_size 1 \
    --gradient_accumulation_steps 4 \
    --learning_rate 1e-5 \
    --max_completion_length 1024  \
    --num_generations 2 \
    --lora_r 32 \
    --lora_alpha 64 \
    --bf16 True \
    --gradient_checkpointing True
# Step 2: Inference demo
python trainer/inference.py \
    --adapter_path models_rl/final \
    --mode demo

END=`date`
echo "Job end at" $END
echo "🎉 All done!"
