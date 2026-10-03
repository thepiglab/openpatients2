#!/usr/bin/env bash
#SBATCH --job-name=op2-pmc-acquire
#SBATCH --account=cai5724
#SBATCH --qos=cai5724
#SBATCH --partition=hpg-default
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --gres=none
#SBATCH --time=06:00:00
#SBATCH --output=pmc-acquire-%j.log
set -euo pipefail
umask 077
cd "${SLURM_SUBMIT_DIR:?Submit from the repository checkout}"
export CUDA_VISIBLE_DEVICES=""
export UV_LINK_MODE=copy
work="${1:?Provide an absolute work directory on cluster storage}"
config="${2:-configs/sources/corpus-stratified-pilot.yaml}"
limit="${3:-25}"
exec uv run --locked --python 3.12 --no-dev op2 corpus run \
  --config "$config" --work-dir "$work" --limit "$limit"
