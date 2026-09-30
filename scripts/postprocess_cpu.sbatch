#!/usr/bin/env bash
#SBATCH --job-name=op2-index
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=logs/op2-index-%j.log
set -euo pipefail
umask 077
cd "${SLURM_SUBMIT_DIR:?}"
export CUDA_VISIBLE_DEVICES=""
if [[ -n "${RUN:-}" ]]; then
  uv run --no-sync op2 export --run "$RUN"
  PATIENTS="$RUN/patients.jsonl"
fi
: "${PATIENTS:?Set PATIENTS to patients.jsonl or set RUN to an extraction run directory}"
: "${INDEX:?Set INDEX to a new .sqlite destination}"
uv run --no-sync op2 validate --input "$PATIENTS" --output "${INDEX}.validation.json"
if [[ -n "${CATALOG:-}" ]]; then
  if [[ -n "${CODED:-}" ]]; then
    uv run --no-sync op2 code-link --input "$PATIENTS" --output "$CODED" --catalog "$CATALOG" --config configs/terminology/linking.yaml
    PATIENTS="$CODED"
  fi
  uv run --no-sync op2 index --input "$PATIENTS" --output "$INDEX" --catalog "$CATALOG"
  if [[ -n "${GRAPH:-}" ]]; then
    uv run --no-sync op2 graph --input "$PATIENTS" --catalog "$CATALOG" --output "$GRAPH"
  fi
else
  uv run --no-sync op2 index --input "$PATIENTS" --output "$INDEX"
fi
