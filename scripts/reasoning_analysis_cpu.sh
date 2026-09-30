#!/usr/bin/env bash
set -euo pipefail
umask 077
: "${STUDY:?Set STUDY=runs/reasoning-k2}"
uv run --no-sync op2 reasoning-analyze --study "$STUDY"
