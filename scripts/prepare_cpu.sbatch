#!/usr/bin/env bash
#SBATCH --job-name=op2-cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=96G
#SBATCH --time=08:00:00
#SBATCH --output=logs/op2-cpu-%j.log
set -euo pipefail
umask 077
cd "${SLURM_SUBMIT_DIR:?}"
: "${MODEL:?Set MODEL=k2 or MODEL=motif before submission}"
export CUDA_VISIBLE_DEVICES=""
export TOKENIZERS_PARALLELISM=true
# Environment/package installation belongs in bootstrap, before this job.
# Pick a CPU partition with appropriate network access under your cluster policy.
case "$MODEL" in
  k2) REPO=IFM/K2-Horizon-375B-A23B-NVFP4 ;;
  motif) REPO=Motif-Technologies/Motif-3-NVFP4 ;;
  *) echo "MODEL must be k2 or motif" >&2; exit 2 ;;
esac
if [[ ! -f data/prepared.jsonl ]]; then
  uv run --no-sync op2 download-dataset --output data/prepared.jsonl
fi
# Enrichment is cached across runs and shared by both model configurations.
# One writer owns data/cache/literature.sqlite; do not launch overlapping prep jobs.
: "${NCBI_EMAIL:?Set NCBI_EMAIL to the tool maintainer email before submission}"
uv run --no-sync op2 enrich-sources --input data/prepared.jsonl \
  --output data/enriched.jsonl --config configs/sources/pmc.yaml
if [[ ! -f "models/$MODEL/op2_snapshot.json" ]]; then
  uv run --no-sync op2 download-model --repo "$REPO" --output "models/$MODEL"
fi
uv run --no-sync op2 pin-config --config "configs/pipelines/$MODEL.yaml" \
  --snapshot "models/$MODEL/op2_snapshot.json" --output "configs/pipelines/$MODEL-pinned.yaml"
uv run --no-sync op2 profile --config "configs/pipelines/$MODEL-pinned.yaml" \
  --tokenizer "models/$MODEL" --output "data/$MODEL.tokens.jsonl" --workers 16
# Container acquisition is also CPU-only. Read docs/DEPLOYMENT.md and pull the
# model-specific versioned SIF before submitting the GPU job.
