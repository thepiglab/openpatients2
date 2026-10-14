#!/usr/bin/env bash
set -euo pipefail
umask 077
phase="${1:?phase}"; work="${2:?work}"; trial="${3:--}"
cd "$work/code"
export PYTHONDONTWRITEBYTECODE=1 TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
if [[ "$phase" != smoke && "$phase" != train ]]; then export CUDA_VISIBLE_DEVICES=""; fi
if ! command -v apptainer >/dev/null; then module load apptainer; fi
# CPU orchestration uses the preparation environment. GPU training executes in
# the pinned container and the CPU-installed training environment.
python="$work.bootstrap/client-venv/bin/python"
if [[ "$phase" == smoke || "$phase" == train ]]; then
  "$python" - "$work" "$phase" "$trial" <<'PY' &
import os,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'src'))
from openpatients2.dense_lora import container_command
work=Path(sys.argv[1]);phase=sys.argv[2]
args=[str(work/'code/scripts/benchmark_dense_lora.py'),phase,'--work',str(work)]
if phase=='train':args+=['--trial',sys.argv[3]]
# exec avoids orphaning the container process when Slurm signals the shell.
os.execvp('apptainer',container_command(work,args,gpu=True))
PY
else
  "$python" scripts/benchmark_dense_lora.py "$phase" --work "$work" &
fi
child=$!
terminate() { trap '' TERM INT; kill -TERM "$child" 2>/dev/null || true; wait "$child" || true; exit 143; }
trap terminate TERM INT
wait "$child"
