#!/usr/bin/env bash
#SBATCH --job-name=op2-reasoning
#SBATCH --partition=hpg-b200
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:b200:8
#SBATCH --cpus-per-task=64
#SBATCH --mem=512G
#SBATCH --time=12:00:00
#SBATCH --output=logs/op2-reasoning-%j.log
#SBATCH --signal=B:TERM@120
set -euo pipefail
umask 077
cd "${SLURM_SUBMIT_DIR:?}"
: "${STUDY:?Set STUDY=runs/reasoning-k2}"
: "${SERVING:?Set SERVING=configs/serving/k2-vllm-tp8.yaml}"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1
command -v uv >/dev/null
command -v apptainer >/dev/null
# No download, tokenizer profiling, export or statistical analysis on the GPU job.
setsid uv run --no-sync op2 reasoning-run --study "$STUDY" --serving "$SERVING" &
pid=$!
cleanup() {
  trap '' TERM INT
  kill -TERM -- "-$pid" 2>/dev/null || true
  wait "$pid" || true
  exit "$1"
}
trap 'cleanup 143' TERM
trap 'cleanup 130' INT
wait "$pid"
