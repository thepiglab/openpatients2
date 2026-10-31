#!/usr/bin/env bash
# Launch independent 12-hour allocations only after checking shared disk capacity.
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
mistral="${1:-$repo/../openpatients2-le-chonk}"
mistral="$(cd -- "$mistral" && pwd -P)"
[[ "$repo" != "$mistral" ]] || { echo 'Use separate Beam and Mistral checkouts.' >&2; exit 1; }
for checkout in "$repo" "$mistral"; do
  git -C "$checkout" diff --quiet HEAD -- src scripts configs pyproject.toml uv.lock || {
    echo "Commit/sync changes before launching: $checkout" >&2; exit 1;
  }
  git -C "$checkout" grep -q 'consume_remaining_budget=True' HEAD -- src/openpatients2/dense_nemotron_super.py || {
    echo "Budget fix missing from committed code: $checkout" >&2; exit 1;
  }
done
python3 - <<'PY'
import shutil
root = '/blue/cai6734/ehr_agent/op2-dense-nemotron-super'
# Beam 290 + 128 reserve; Mistral 750 + 128 reserve. No cross-campaign dedup.
required = 1296
free = shutil.disk_usage(root).free / 2**30
print(f'Shared disk: {free:.1f} GiB free; both campaigns require {required} GiB.')
if free < required:
    raise SystemExit('Insufficient shared storage for concurrent campaigns. Free reviewed old artifacts or run sequentially. No jobs submitted.')
PY
# Credentials must already be exported by the caller. Do not print them.
DENSE_REF=HEAD DENSE_ACCOUNT=cai5724 DENSE_QOS=cai5724 DENSE_HOURS=12 \
  bash "$repo/scripts/run_dense_beam.sh" overnight
echo 'Beam submitted. Submitting Mistral from its independent checkout.'
DENSE_REF=HEAD DENSE_ACCOUNT=cai6734 DENSE_QOS=cai6734 DENSE_HOURS=12 \
  bash "$mistral/scripts/run_dense_le_chonk.sh" overnight
