#!/usr/bin/env bash
#SBATCH --job-name=op2-terminology
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=logs/op2-terminology-%j.log
set -euo pipefail
umask 077
cd "${SLURM_SUBMIT_DIR:?}"
export CUDA_VISIBLE_DEVICES=""
: "${CATALOG:?Set CATALOG to a new .sqlite path}"
uv run --no-sync op2 vocab-build --config "${VOCAB_CONFIG:-configs/terminology/catalog.yaml}" --output "$CATALOG"
if [[ -n "${PATIENTS:-}" ]]; then
  : "${CODED:?Set CODED to a new JSONL output}"
  uv run --no-sync op2 code-link --input "$PATIENTS" --output "$CODED" --catalog "$CATALOG" --config configs/terminology/linking.yaml
fi
