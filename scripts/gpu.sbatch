#!/usr/bin/env bash
#SBATCH --job-name=op2-b200
#SBATCH --partition=hpg-b200
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:b200:8
#SBATCH --cpus-per-task=64
#SBATCH --mem=512G
#SBATCH --time=12:00:00
#SBATCH --output=logs/op2-gpu-%j.log
#SBATCH --signal=B:TERM@120
set -euo pipefail
umask 077
cd "${SLURM_SUBMIT_DIR:?}"
: "${PIPELINE:?Set PIPELINE=configs/pipelines/k2-pinned.yaml}"
: "${SERVING:?Set SERVING=configs/serving/k2-vllm-tp8.yaml}"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1
# Load the UF-provided Apptainer module here if not already present in your shell.
command -v apptainer >/dev/null || { echo "Load your Apptainer module before submission" >&2; exit 2; }
command -v uv >/dev/null
command -v setsid >/dev/null
# Everything heavy (dataset, model, SIF, deps, tokenization) must already be local.
MODE="${MODE:-benchmark}"
if [[ "$MODE" == campaign ]]; then
  : "${CAMPAIGN:?Set CAMPAIGN=configs/campaign-k2.yaml}"
  setsid uv run --no-sync op2 campaign --config "$CAMPAIGN" &
else
  args=(launch-run --config "$PIPELINE" --serving "$SERVING")
  if [[ "$MODE" == benchmark ]]; then args+=(--benchmark --limit "${LIMIT:-128}"); fi
  if [[ "$MODE" != benchmark && "$MODE" != production ]]; then echo "Bad MODE" >&2; exit 2; fi
  setsid uv run --no-sync op2 "${args[@]}" &
fi
pid=$!
cleanup() {
  local status="$1"
  trap '' TERM INT
  # Signal the client process group, not only the uv wrapper. Python unwinds its
  # runner and explicitly stops the separately sessioned inference servers.
  kill -TERM -- "-$pid" 2>/dev/null || true
  wait "$pid" || true
  exit "$status"
}
trap 'cleanup 143' TERM
trap 'cleanup 130' INT
wait "$pid"
# Returning from this job releases the GPU allocation; no persistent server left running.
