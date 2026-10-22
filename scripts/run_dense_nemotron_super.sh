#!/usr/bin/env bash
# CPU preparation/access -> per model CPU download, GPU benchmark, CPU cleanup -> CPU scores/export.
set -euo pipefail
umask 077

if [[ "${1:-}" == --bootstrap ]]; then
  repo="$2"; work="$3"; revision="$4"; bootstrap="$5"; account="$6"; qos="$7"; hours="$8"
  export CUDA_VISIBLE_DEVICES="" UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1
  export UV_CACHE_DIR="$bootstrap/uv-cache" UV_PROJECT_ENVIRONMENT="$bootstrap/client-venv"
  snapshot="$bootstrap/source"
  mkdir -p "$snapshot"
  git -C "$repo" archive "$revision" | tar -x -C "$snapshot"
  cd "$snapshot"
  export DENSE_SOURCE_REVISION="$revision"
  uv sync --locked --extra data --no-dev --python 3.12
  selection=()
  selection+=(--scheduler "${DENSE_SUPER_SCHEDULER:-packed}")
  read -r -a splits <<< "${DENSE_SUPER_SPLITS:-train}"
  selection+=(--splits "${splits[@]}" --selection-seed "${DENSE_SUPER_SELECTION_SEED:-42}")
  if [[ -n "${DENSE_SUPER_EXPECTED:-}" ]]; then selection+=(--expected "$DENSE_SUPER_EXPECTED"); fi
  if [[ -n "${DENSE_SUPER_ARTICLE_IDS:-}" ]]; then
    read -r -a articles <<< "$DENSE_SUPER_ARTICLE_IDS"
    selection+=(--article-ids "${articles[@]}")
  fi
  if [[ "${DENSE_INCLUDE_TEST:-0}" == 1 ]]; then selection+=(--include-test); fi
  case "${DENSE_SUPER_WITH_IMAGES:-1}" in
    1) selection+=(--with-images);;
    0) selection+=(--text-only);;
    *) echo 'DENSE_SUPER_WITH_IMAGES must be 1 (supported modalities, default) or 0 (text only).' >&2; exit 1;;
  esac
  if [[ -n "${DENSE_SUPER_LIMIT_ARTICLES:-}" ]]; then selection+=(--limit-articles "$DENSE_SUPER_LIMIT_ARTICLES"); fi
  if [[ -n "${DENSE_SUPER_PROFILES:-}" ]]; then
    read -r -a profiles <<< "$DENSE_SUPER_PROFILES"
    selection+=(--profiles "${profiles[@]}")
  fi
  "$UV_PROJECT_ENVIRONMENT/bin/python" scripts/benchmark_dense_nemotron_super.py prepare \
    --root "$snapshot" --work "$work" --hours "$hours" "${selection[@]}"
  "$UV_PROJECT_ENVIRONMENT/bin/python" scripts/benchmark_dense_nemotron_super.py submit \
    --work "$work" --account "$account" --qos "$qos"
  exit
fi

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
pointer="$repo/runs/dense-nemotron-super-last-work.txt"
case "${1:-}" in
  status|verify|resume|cancel)
    action="$1"; work="${2:-}"
    if [[ -z "$work" ]]; then
      [[ -f "$pointer" ]] || { echo 'No Super campaign has been launched from this checkout.' >&2; exit 1; }
      work="$(cat "$pointer")"
    fi
    if [[ "$action" == cancel ]]; then
      # Use current checkout code: older frozen campaigns predate this command.
      exec python3 "$repo/scripts/benchmark_dense_nemotron_super.py" cancel --work "$work"
    fi
    [[ -f "$work/prepared.json" ]] || { echo "Preparation is pending. Log: $work.bootstrap/prepare.log"; exit 1; }
    if [[ "$action" == resume ]]; then
      exec python3 "$work/code/scripts/benchmark_dense_nemotron_super.py" submit --work "$work" --resume \
        --account "${DENSE_ACCOUNT:-cai5724}" --qos "${DENSE_QOS:-cai5724}"
    fi
    exec python3 "$work/code/scripts/benchmark_dense_nemotron_super.py" "$action" --work "$work"
    ;;
esac

mkdir -p "$repo/runs"
launch_lock="$repo/runs/dense-nemotron-super-launch.lock"
mkdir "$launch_lock" 2>/dev/null || { echo "Another launch is in progress, or its lock remains: $launch_lock" >&2; exit 1; }
trap 'rmdir "$launch_lock"' EXIT
if [[ -f "$pointer" ]]; then
  previous="$(cat "$pointer")"
  ids="$(python3 - "$previous" <<'PY'
import json,pathlib,sys
p=pathlib.Path(sys.argv[1]); ids=[]
bootstrap=pathlib.Path(str(p)+'.bootstrap/job-id.txt')
if bootstrap.exists(): ids.append(bootstrap.read_text().strip().split(';')[0])
for f in (p/'submissions').glob('*.json'):
    ids.extend(str(j['id']) for j in json.loads(f.read_text())['jobs'])
print(','.join(i for i in ids if i.isdigit()))
PY
)"
  if [[ -n "$ids" ]]; then
    live="$(squeue -h -u "$USER" -o '%i')"
    while read -r id; do
      [[ -n "$id" ]] || continue
      case ",$ids," in *",$id,"*) echo "Previous Super campaign is still active: $previous" >&2; exit 1;; esac
    done <<< "$live"
  fi
fi

work="${1:-/blue/cai6734/ehr_agent/op2-dense-nemotron-super/run-$(date +%Y%m%d-%H%M%S)}"
[[ "$work" == /* ]] || work="$PWD/$work"
account="${DENSE_ACCOUNT:-cai5724}"; qos="${DENSE_QOS:-cai5724}"; hours="${DENSE_HOURS:-12}"
bootstrap="$work.bootstrap"
[[ ! -e "$work" && ! -e "$bootstrap" ]] || { echo "Choose a new campaign path: $work" >&2; exit 1; }
ref="${DENSE_REF:-HEAD}"
revision="$(git -C "$repo" rev-parse "$ref^{commit}")"
for required in scripts/benchmark_dense_nemotron_super.py scripts/dense_nemotron_super.sbatch scripts/vllm_rust_frontend.py \
    scripts/run_dense_nemotron_super.sh src/openpatients2/dense_nemotron_super.py \
    src/openpatients2/benchmark_selection.py src/openpatients2/staged_timeline.py \
    src/openpatients2/super_packing.py \
    src/openpatients2/semantic_benchmark.py src/openpatients2/speculation_metrics.py \
    scripts/freeze_super_reference.py docs/SEMANTIC_BENCHMARK.md \
    src/openpatients2/benchmark_reporting.py src/openpatients2/reasoning_lineage.py \
    src/openpatients2/nemotron_super_recipes.py src/openpatients2/muse_spark_recipe.py \
    src/openpatients2/redhat_nemotron_recipe.py src/openpatients2/beam_recipe.py docs/DENSE_NEMOTRON_SUPER.md \
    configs/hipergator/nemotron-super/provenance.json \
    configs/hipergator/nemotron-super/reference.jsonl.gz \
    configs/hipergator/nemotron-super/reference-manifest.json; do
  git -C "$repo" cat-file -e "$revision:$required" 2>/dev/null || {
    echo 'The selected commit lacks the complete Super benchmark. Commit/push the implementation and pull it on HiPerGator before launching.' >&2
    exit 1
  }
done
if ! git -C "$repo" diff --quiet "$revision" -- src scripts configs docs/DENSE_NEMOTRON_SUPER.md pyproject.toml uv.lock; then
  echo 'Commit the benchmark changes before launching: this launcher freezes committed code.' >&2; exit 1
fi
mkdir -p "$bootstrap"
cp "$repo/scripts/run_dense_nemotron_super.sh" "$bootstrap/bootstrap.sh"
printf '%s\n' "$revision" > "$bootstrap/source-commit.txt"
while IFS='=' read -r key value; do
  case "$key" in SBATCH_*) unset "$key";; esac
done < <(env)
job="$(sbatch --parsable --account="$account" --qos="$qos" --partition=hpg-default \
  --nodes=1 --ntasks=1 --cpus-per-task=8 --mem=32G --time=04:00:00 \
  --job-name=op2-super-prepare --output="$bootstrap/prepare.log" \
  "$bootstrap/bootstrap.sh" --bootstrap "$repo" "$work" "$revision" "$bootstrap" "$account" "$qos" "$hours")"
printf '%s\n' "$job" > "$bootstrap/job-id.txt"
printf '%s\n' "$work" > "$pointer"
printf 'Preparation job: %s\nSource commit: %s\nCampaign: %s\nLog: %s/prepare.log\n' "$job" "$revision" "$work" "$bootstrap"
echo 'Default: four configurations run once per article: NVIDIA NVFP4 + MTP5 (1 GPU), NVIDIA BF16 + MTP5 (2 GPUs), Muse Spark Open + DSpark7 (8 GPUs) use text and images; Beam NVFP4 + DFlash2 (4 GPUs) uses text only. DENSE_SUPER_WITH_IMAGES=0 explicitly selects text-only runs. Packed default: Beam 4 GPUs + BF16 2 GPUs + two NVFP4 replicas on 1 GPU each; Spark runs alone on 8. CPU staging and cleanup are serial barriers. DENSE_SUPER_SCHEDULER=serial restores one-model-at-a-time storage use. At most eight B200s run at once. Every worker checks streaming reasoning capture before article inference.'
