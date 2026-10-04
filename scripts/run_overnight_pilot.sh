#!/usr/bin/env bash
# One command: CPU preparation -> CPU setup/download -> GPU -> both cleanups.
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo"
export UV_LINK_MODE=copy
work="${1:-$(dirname "$repo")/op2-clinical-overnight/run-$(date +%Y%m%d-%H%M%S)}"
# Reproducible prepare/submit validation happens before any job is released.
exec uv run --locked --python 3.12 --no-dev op2 corpus-pilot submit \
  --config configs/pilot/overnight.yaml --work-dir "$work"
