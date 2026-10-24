#!/usr/bin/env bash
# Full development campaign: three profiles, eight GPUs, no test-set exposure.
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
export DENSE_SUPER_PROFILES='nvfp4 bf16 beam-nvfp4-dflash2'
export DENSE_SUPER_SCHEDULER=packed DENSE_SUPER_WITH_IMAGES=1
export DENSE_SUPER_SPLITS='train validation' DENSE_INCLUDE_TEST=0
# Clear pilot selectors inherited from an earlier shell/session.
export DENSE_SUPER_ARTICLE_IDS='' DENSE_SUPER_LIMIT_ARTICLES='' DENSE_SUPER_EXPECTED=''
export DENSE_HOURS="${DENSE_HOURS:-72}"
exec bash "$repo/scripts/run_dense_nemotron_super.sh" "$@"
