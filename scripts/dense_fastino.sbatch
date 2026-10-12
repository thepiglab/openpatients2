#!/usr/bin/env bash
# Resources and dependencies are supplied by benchmark_dense_fastino.py submit.
set -euo pipefail
umask 077
phase="${1:?phase}"
work="${2:?absolute work directory}"
shard="${3:--}"
cd "$work/code"
export UV_PROJECT_ENVIRONMENT="$work/runtime/client-venv"
export UV_CACHE_DIR="$work/runtime/uv-cache" UV_LINK_MODE=copy
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false PYTHONDONTWRITEBYTECODE=1
if [[ "$phase" != worker ]]; then export CUDA_VISIBLE_DEVICES=""; fi
if [[ "$phase" == setup || "$phase" == worker ]]; then
  if ! command -v apptainer >/dev/null; then module load apptainer; fi
fi
if [[ "$phase" == setup ]]; then
  command -v uv >/dev/null
  uv sync --locked --extra data --no-dev --python 3.12
fi
if [[ "$phase" == worker ]]; then
  export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1
fi
# Aggregate can still diagnose/export failures if CPU setup never made a venv.
python="$work/runtime/client-venv/bin/python"
if [[ ! -x "$python" ]]; then python=python3; fi
args=("$phase" --work "$work")
if [[ "$shard" != - ]]; then args+=(--shard "$shard"); fi
"$python" scripts/benchmark_dense_fastino.py "${args[@]}" &
child=$!
terminate() {
  trap '' TERM INT
  kill -TERM "$child" 2>/dev/null || true
  wait "$child" || true
  exit 143
}
trap terminate TERM INT
wait "$child"
