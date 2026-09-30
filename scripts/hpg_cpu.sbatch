#!/usr/bin/env bash
# Resources, cai5724 account/QoS and dependencies are supplied by op2 hpg-benchmark.
# No GPU resources are requested for setup, download, deletion or final analysis.
set -euo pipefail
umask 077
stage="${1:?CPU stage required}"
work="${2:?Campaign work directory required}"
model="${3:-}"
cd "${SLURM_SUBMIT_DIR:?Submit from the extracted benchmark package}"
export CUDA_VISIBLE_DEVICES=""
export UV_CACHE_DIR="$work/uv-cache"
command -v uv >/dev/null
if [[ "$stage" == setup ]]; then
  # All dependency/Python acquisition happens on CPU, before any GPU job.
  uv sync --locked --extra data --no-dev --python 3.12
  if ! command -v apptainer >/dev/null; then module load apptainer; fi
fi
args=(hpg-benchmark stage "$stage" --work-dir "$work")
if [[ -n "$model" ]]; then args+=(--model "$model"); fi
exec uv run --no-sync --offline op2 "${args[@]}"
