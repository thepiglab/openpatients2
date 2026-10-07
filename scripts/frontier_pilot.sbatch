#!/usr/bin/env bash
# Every GPU job is independent: one node, one task, one B200.
set -euo pipefail
umask 077
phase="${1:?phase required}"
work="${2:?absolute campaign directory required}"
shard="${3:--}"
cd "${SLURM_SUBMIT_DIR:?Submit from the repository checkout}"
export UV_PROJECT_ENVIRONMENT="$work/client-venv"
export UV_CACHE_DIR="$work/uv-cache"
export UV_LINK_MODE=copy
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false
if [[ "$phase" != worker ]]; then
  export CUDA_VISIBLE_DEVICES=""
fi
if [[ "$phase" == prepare ]]; then
  uv sync --locked --extra data --extra plots --no-dev --python 3.12
fi
if [[ "$phase" == setup || "$phase" == worker ]]; then
  if ! command -v apptainer >/dev/null; then module load apptainer; fi
fi
if [[ "$phase" == worker ]]; then
  export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1
fi
args=(--phase "$phase" --work-dir "$work")
if [[ "$shard" != - ]]; then args+=(--shard "$shard"); fi
setsid uv run --python 3.12 --no-sync --offline python -m openpatients2.frontier_campaign stage "${args[@]}" &
pilot_pid=$!
terminate() {
  trap '' TERM INT
  kill -TERM -- "-$pilot_pid" 2>/dev/null || true
  wait "$pilot_pid" || true
  exit "$1"
}
trap 'terminate 143' TERM
trap 'terminate 130' INT
wait "$pilot_pid"
