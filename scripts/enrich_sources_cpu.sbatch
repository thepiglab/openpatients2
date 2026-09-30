#!/usr/bin/env bash
#SBATCH --job-name=op2-sources
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=08:00:00
#SBATCH --output=logs/op2-sources-%j.log
set -euo pipefail
umask 077
cd "${SLURM_SUBMIT_DIR:?}"
: "${NCBI_EMAIL:?Set NCBI_EMAIL to your actual maintainer email}"
export CUDA_VISIBLE_DEVICES=""
# Prepare uv and download the source dataset on CPU first. Configure account/QOS
# and an internet-enabled CPU partition at submission, not a GPU partition.
# Submit only one writer per literature cache path.
uv run --no-sync op2 enrich-sources \
  --input "${SOURCE_INPUT:-data/prepared.jsonl}" \
  --output "${SOURCE_OUTPUT:-data/enriched.jsonl}" \
  --config configs/sources/pmc.yaml
