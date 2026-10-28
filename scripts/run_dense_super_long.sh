#!/usr/bin/env bash
# Compatibility entry point: Beam-only development campaign, eight GPUs.
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
exec bash "$repo/scripts/run_dense_beam.sh" long "$@"
