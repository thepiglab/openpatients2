#!/usr/bin/env bash
# One CPU prepare/setup/download -> four independent B200s -> CPU score/cleanup.
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo"
export UV_LINK_MODE=copy
work="${1:-$(dirname "$repo")/op2-architecture/run-$(date +%Y%m%d-%H%M%S)}"
if [[ $# -gt 0 ]]; then shift; fi
exec uv run --locked --python 3.12 --no-dev python -m openpatients2.frontier_campaign submit \
  --config configs/pilot/clinical-architecture.yaml --work-dir "$work" "$@"
