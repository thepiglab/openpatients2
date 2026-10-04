#!/usr/bin/env bash
# Fork ended results; CPU recovery/download -> one-GPU GEPA -> eight-GPU trials -> cleanup.
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo"
export UV_LINK_MODE=copy
parent="${1:?Pass the ended campaign directory to resume}"
work="${2:-$(dirname "$repo")/op2-clinical-overnight/run-$(date +%Y%m%d-%H%M%S)}"
exec uv run --locked --python 3.12 --no-dev op2 corpus-pilot submit-resume \
  --from-work-dir "$parent" --work-dir "$work"
