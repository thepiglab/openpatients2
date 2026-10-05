#!/usr/bin/env bash
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo"
export UV_LINK_MODE=copy
work="${1:-$(dirname "$repo")/op2-gepa-clinical/run-$(date +%Y%m%d-%H%M%S)}"
exec uv run --locked --python 3.12 --no-dev op2 corpus-pilot submit \
  --config configs/pilot/overnight-gepa-clinical.yaml --work-dir "$work"
