#!/usr/bin/env bash
# Resource requests and dependencies come from op2 corpus-pilot.
set -euo pipefail
umask 077
phase="${1:?phase required}"
work="${2:?absolute campaign directory required}"
cd "${SLURM_SUBMIT_DIR:?Submit from the repository checkout}"
export UV_PROJECT_ENVIRONMENT="$work/client-venv"
export UV_CACHE_DIR="$work/uv-cache"
export UV_LINK_MODE=copy
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false
if [[ "$phase" != gpu && "$phase" != bootstrap && "$phase" != gepa ]]; then
  export CUDA_VISIBLE_DEVICES=""
  # Dependencies are downloaded/synchronized only in CPU allocations.
  uv sync --locked --extra data --extra plots --extra optimize --no-dev --python 3.12
fi
if [[ "$phase" != cpu ]]; then
  if ! command -v apptainer >/dev/null; then module load apptainer; fi
fi
if [[ "$phase" == gpu || "$phase" == bootstrap || "$phase" == gepa ]]; then
  export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1
fi
setsid uv run --python 3.12 --no-sync --offline op2 corpus-pilot stage "$phase" --work-dir "$work" &
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
