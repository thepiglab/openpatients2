#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${ACCOUNT:?Set your valid HiPerGator account}"
: "${QOS:?Set your valid HiPerGator QOS}"
mkdir -p logs
# No invented account, QOS, partition time entitlement, or hard-coded campus credentials.
sbatch --account="$ACCOUNT" --qos="$QOS" --export=ALL scripts/gpu.sbatch
