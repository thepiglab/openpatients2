#!/usr/bin/env bash
# CPU rehash/metadata recovery, then resume the original frozen Fastino campaign.
set -euo pipefail
umask 077
if [[ "${1:-}" == --cpu ]]; then
  repo="$2"; work="$3"; account="$4"; qos="$5"
  export CUDA_VISIBLE_DEVICES="" PYTHONDONTWRITEBYTECODE=1
  cd "$repo"
  exec "$work/runtime/client-venv/bin/python" scripts/benchmark_dense_fastino.py \
    repair --work "$work" --resubmit --account "$account" --qos "$qos"
fi
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
work="${1:?Provide the existing Fastino campaign directory}"
[[ "$work" == /* ]] || work="$PWD/$work"
[[ -f "$work/runtime.json" ]] || { echo 'Missing completed CPU setup/runtime.json' >&2; exit 1; }
account="${DENSE_ACCOUNT:-cai5724}"; qos="${DENSE_QOS:-cai5724}"
while IFS='=' read -r key value; do
  case "$key" in SBATCH_*) unset "$key";; esac
done < <(env)
sbatch --parsable --account="$account" --qos="$qos" --partition=hpg-default \
  --nodes=1 --ntasks=1 --cpus-per-task=2 --mem=8G --time=02:00:00 \
  --job-name=op2-fastino-repair --output="$work/logs/repair-%j.log" \
  "$repo/scripts/repair_dense_fastino.sh" --cpu "$repo" "$work" "$account" "$qos"
