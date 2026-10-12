#!/usr/bin/env bash
# One command: CPU preparation -> model setup -> four B200 workers -> score/export.
set -euo pipefail
umask 077

if [[ "${1:-}" == --bootstrap ]]; then
  repo="$2"; work="$3"; revision="$4"; bootstrap="$5"
  account="$6"; qos="$7"; hours="$8"
  export CUDA_VISIBLE_DEVICES="" UV_LINK_MODE=copy
  export UV_CACHE_DIR="$bootstrap/uv-cache"
  snapshot="$bootstrap/source"
  mkdir -p "$snapshot"
  git -C "$repo" archive "$revision" | tar -x -C "$snapshot"
  cd "$snapshot"
  export UV_PROJECT_ENVIRONMENT="$bootstrap/client-venv"
  uv sync --locked --extra data --no-dev --python 3.12
  "$UV_PROJECT_ENVIRONMENT/bin/python" scripts/benchmark_dense.py prepare \
    --root "$snapshot" --work "$work" --expected 260 --download-media
  "$UV_PROJECT_ENVIRONMENT/bin/python" scripts/benchmark_dense.py submit \
    --work "$work" --account "$account" --qos "$qos" --hours "$hours"
  exit
fi

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
pointer="$repo/runs/dense-benchmark-last-work.txt"
case "${1:-}" in
  status|verify)
    action="$1"
    work="${2:-}"
    if [[ -z "$work" ]]; then work="$(cat "$pointer")"; fi
    if [[ ! -f "$work/code/scripts/benchmark_dense.py" ]]; then
      echo "Preparation has not finished. Log: $work.bootstrap/prepare.log"
      squeue -u "$USER"
      exit 1
    fi
    exec python3 "$work/code/scripts/benchmark_dense.py" "$action" --work "$work"
    ;;
esac

work="${1:-/blue/cai6734/ehr_agent/op2-dense-benchmark/run-$(date +%Y%m%d-%H%M%S)}"
[[ "$work" == /* ]] || work="$PWD/$work"
account="${DENSE_ACCOUNT:-cai5724}"
qos="${DENSE_QOS:-cai5724}"
hours="${DENSE_HOURS:-12}"
bootstrap="$work.bootstrap"
if [[ -e "$work" || -e "$bootstrap" ]]; then
  echo "Campaign already exists; choose a new output path: $work" >&2
  exit 1
fi

# This checkout may still be main. Use the hardened benchmark branch without
# switching or modifying the user's checkout. Pin a commit before queueing.
ref="${DENSE_REF:-HEAD}"
if ! git -C "$repo" cat-file -e "$ref:src/openpatients2/dense_benchmark.py" 2>/dev/null; then
  for candidate in origin/dense500-shared-rules dense500-shared-rules; do
    if git -C "$repo" cat-file -e "$candidate:src/openpatients2/dense_benchmark.py" 2>/dev/null; then
      ref="$candidate"
      break
    fi
  done
fi
git -C "$repo" cat-file -e "$ref:configs/hipergator/dense-benchmark/reference.jsonl.gz" || {
  echo 'Fetch the benchmark branch containing the launcher and frozen reference first.' >&2
  exit 1
}
revision="$(git -C "$repo" rev-parse "$ref^{commit}")"
mkdir -p "$bootstrap" "$repo/runs"
cp "$repo/scripts/run_dense_benchmark.sh" "$bootstrap/bootstrap.sh"
printf '%s\n' "$work" > "$pointer"
printf '%s\n' "$revision" > "$bootstrap/source-commit.txt"
# Remove inherited sbatch overrides, as in the older campaign launchers.
while IFS='=' read -r key value; do
  case "$key" in SBATCH_*) unset "$key";; esac
done < <(env)
args=(--parsable --account="$account" --qos="$qos" --partition=hpg-default
      --nodes=1 --ntasks=1 --cpus-per-task=8 --mem=32G --time=02:00:00
      --job-name=op2-dense-prepare --output="$bootstrap/prepare.log"
      "$bootstrap/bootstrap.sh" --bootstrap "$repo" "$work" "$revision" "$bootstrap"
      "$account" "$qos" "$hours")
sbatch --test-only "${args[@]}"
job="$(sbatch "${args[@]}")"
printf '%s\n' "$job" > "$bootstrap/job-id.txt"
printf 'Preparation job: %s\nSource: %s (%s)\nCampaign: %s\nLog: %s/prepare.log\n' \
  "$job" "$ref" "$revision" "$work" "$bootstrap"
echo 'Preparation automatically submits model setup, four GPU workers, and scoring/export.'
