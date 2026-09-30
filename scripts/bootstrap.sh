#!/usr/bin/env bash
set -euo pipefail
# Run this on CPU / an internet-enabled preparation node, never during paid GPU startup.
cd "$(dirname "$0")/.."
uv sync --extra data
uv run pytest -q
# uv sync generates a REAL resolver-produced uv.lock. Commit it for repeatable deployments.
uv export --frozen --extra data --no-dev --format requirements-txt > requirements.resolved.txt
