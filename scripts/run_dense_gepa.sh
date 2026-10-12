#!/usr/bin/env bash
# One command: CPU preparation -> model setup -> one B200 with four concurrent optimizers -> compare/export (eight-hour GPU cap).
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
  export DENSE_SOURCE_REVISION="$revision"
  export UV_PROJECT_ENVIRONMENT="$bootstrap/client-venv"
  uv sync --locked --extra data --extra optimize --no-dev --python 3.12
  "$UV_PROJECT_ENVIRONMENT/bin/python" scripts/benchmark_dense_gepa.py prepare \
    --root "$snapshot" --work "$work" --expected 260 --hours "$hours" --beta "${DENSE_BETA:-2}" --metric-calls "${DENSE_METRIC_CALLS:-1500}"
  "$UV_PROJECT_ENVIRONMENT/bin/python" scripts/benchmark_dense_gepa.py submit \
    --work "$work" --account "$account" --qos "$qos"
  exit
fi

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
pointer="$repo/runs/dense-gepa-last-work.txt"
case "${1:-}" in
  status|verify)
    action="$1"
    work="${2:-}"
    if [[ -z "$work" ]]; then
      if [[ ! -f "$pointer" ]]; then
        echo 'No benchmark has been launched from this checkout. Start it with:' >&2
        echo '  bash scripts/run_dense_gepa.sh' >&2
        exit 1
      fi
      work="$(cat "$pointer")"
    fi
    if [[ ! -f "$work/prepared.json" ]]; then
      echo "Preparation has not finished. Log: $work.bootstrap/prepare.log"
      squeue -u "$USER"
      exit 1
    fi
    exec python3 "$work/code/scripts/benchmark_dense_gepa.py" "$action" --work "$work"
    ;;
esac

mkdir -p "$repo/runs"
launch_lock="$repo/runs/dense-gepa-launch.lock"
if ! mkdir "$launch_lock" 2>/dev/null; then
  echo "Another launch is in progress (or left a stale lock): $launch_lock" >&2
  exit 1
fi
trap 'rmdir "$launch_lock"' EXIT

if [[ -f "$pointer" ]]; then
  previous="$(cat "$pointer")"
  ids="$(python3 - "$previous" <<'PYIDS'
import json, pathlib, sys
p=pathlib.Path(sys.argv[1]); ids=[]
f=pathlib.Path(str(p)+'.bootstrap/job-id.txt')
if f.exists(): ids.append(f.read_text().strip().split(';')[0])
for f in (p/'submissions').glob('*.json'):
    ids.extend(str(j['id']) for j in json.loads(f.read_text())['jobs'])
print(','.join(i for i in ids if i.isdigit()))
PYIDS
)"
  if [[ -n "$ids" ]]; then
    live="$(squeue -h -u "$USER" -o '%i')" || {
      echo 'Could not check active jobs; refusing a potentially duplicate launch.' >&2
      exit 1
    }
    while read -r id; do
      [[ -n "$id" ]] || continue
      case ",$ids," in
        *",$id,"*) echo "Previous GEPA campaign is still active: $previous" >&2; exit 1;;
      esac
    done <<< "$live"
  fi
fi

work="${1:-/blue/cai6734/ehr_agent/op2-dense-gepa/run-$(date +%Y%m%d-%H%M%S)}"
[[ "$work" == /* ]] || work="$PWD/$work"
account="${DENSE_ACCOUNT:-cai5724}"
qos="${DENSE_QOS:-cai5724}"
hours="${DENSE_HOURS:-8}"
bootstrap="$work.bootstrap"
if [[ -e "$work" || -e "$bootstrap" ]]; then
  echo "Campaign already exists; choose a new output path: $work" >&2
  exit 1
fi

# The complete benchmark package lives on main. Pin this checkout's committed
# revision before queueing so code, reference, and source packets stay together.
package_exists() {
  local candidate="$1" required
  for required in scripts/benchmark_dense_gepa.py scripts/dense_gepa.sbatch \
      src/openpatients2/dense_gepa.py \
      src/openpatients2/dense_benchmark.py src/openpatients2/dense_benchmark_serving.py \
      src/openpatients2/dense_benchmark_telemetry.py \
      configs/hipergator/dense-benchmark/reference.jsonl.gz \
      configs/hipergator/dense-benchmark/chat_template.jinja \
      docs/DENSE260_HIPERGATOR_BENCHMARK.md docs/DENSE_GEPA.md; do
    git -C "$repo" cat-file -e "$candidate:$required" 2>/dev/null || return 1
  done
}
ref="${DENSE_REF:-HEAD}"
package_exists "$ref" || {
  echo 'This commit does not contain the complete benchmark package. Update main:' >&2
  echo '  git switch main' >&2
  echo '  git pull --ff-only origin main' >&2
  echo '  bash scripts/run_dense_gepa.sh' >&2
  exit 1
}
revision="$(git -C "$repo" rev-parse "$ref^{commit}")"
if ! git -C "$repo" diff --quiet "$revision" -- src scripts configs docs/DENSE_GEPA.md pyproject.toml uv.lock; then
  echo 'The launch uses committed code. Commit/push the campaign changes yourself, then pull them here before launching.' >&2
  exit 1
fi
mkdir -p "$bootstrap" "$repo/runs"
cp "$repo/scripts/run_dense_gepa.sh" "$bootstrap/bootstrap.sh"
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
echo 'Preparation automatically submits model setup, one GPU running four concurrent task optimizers, and comparison/export.'
