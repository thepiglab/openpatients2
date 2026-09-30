#!/usr/bin/env bash
# op2 hpg-benchmark supplies --partition=hpg-b200 and the model's GPU count,
# --account=cai5724 --qos=cai5724 and the selected model's topology.
set -euo pipefail
umask 077
[[ "${1:?}" == gpu ]]
work="${2:?Campaign work directory required}"
model="${3:?Model name required}"
cd "${SLURM_SUBMIT_DIR:?Submit from the extracted benchmark package}"
export UV_CACHE_DIR="$work/uv-cache"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
if ! command -v apptainer >/dev/null; then module load apptainer; fi
command -v uv >/dev/null
command -v setsid >/dev/null
# TERM unwinds Python and shuts down all independently sessioned vLLM servers.
# Do not let the uv wrapper absorb Slurm's advance signal.
setsid uv run --no-sync --offline op2 hpg-benchmark stage gpu --work-dir "$work" --model "$model" &
pid=$!
terminate() {
  local status="$1"
  trap '' TERM INT
  kill -TERM -- "-$pid" 2>/dev/null || true
  wait "$pid" || true
  exit "$status"
}
trap 'terminate 143' TERM
trap 'terminate 130' INT
wait "$pid"
