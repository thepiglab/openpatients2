#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_LINK_MODE=copy
command -v uv >/dev/null
uv sync --locked --no-dev --python 3.12
exec uv run --python 3.12 --no-sync op2 hpg-benchmark submit \
  --config configs/hipergator/glimmer.yaml "$@"
