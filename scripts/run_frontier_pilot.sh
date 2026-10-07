#!/usr/bin/env bash
# One command: CPU prepare/setup/download -> independent GPUs -> CPU score/cleanup.
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo"
export UV_LINK_MODE=copy
work="${1:-$(dirname "$repo")/op2-frontier/run-$(date +%Y%m%d-%H%M%S)}"
if [[ $# -gt 0 ]]; then shift; fi
exec uv run --locked --python 3.12 --no-dev python -m openpatients2.frontier_campaign submit \
  --config configs/pilot/frontier.yaml --work-dir "$work" "$@"
