#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export CUDA_VISIBLE_DEVICES=""
mkdir -p containers
case "${1:?Usage: scripts/pull_container_cpu.sh k2|motif}" in
  k2) destination=containers/k2-vllm030.sif; image=docker://vllm/vllm-openai:v0.30.0 ;;
  motif) destination=containers/motif-vllm026.sif; image=docker://ghcr.io/motiftechnologies/vllm:v0.26.0-motif3 ;;
  *) echo "Use k2 or motif; SGLang challenger needs an independently verified image." >&2; exit 2 ;;
esac
if [[ ! -f "$destination" ]]; then apptainer pull "$destination" "$image"; fi
sha256sum "$destination" > "${destination}.sha256"
printf '%s\n' "$image" > "${destination}.source.txt"
# For release-grade reproduction, prefer an audited OCI @sha256 digest over a mutable tag.
# The local SIF SHA records exactly what this download produced.
