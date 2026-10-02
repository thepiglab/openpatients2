#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_LINK_MODE=copy
uv sync --locked --no-dev --python 3.12
exec uv run --locked --python 3.12 --no-sync op2 hpg-benchmark submit \
  --config configs/hipergator/glimmer-fp8-tuning.yaml "$@"
