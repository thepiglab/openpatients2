#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null
# Prepare this small CPU client environment on the login node; the scheduled CPU
# setup stage installs model-download dependencies and acquires the container.
uv sync --locked --no-dev --python 3.12
exec uv run --python 3.12 --no-sync op2 hpg-benchmark submit "$@"
