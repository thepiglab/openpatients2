#!/usr/bin/env bash
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
mode="${1:-smoke}"
if [[ $# -gt 0 ]]; then shift; fi
export DENSE_SUPER_PROFILES=le-chonk-nvfp4-dspark
export DENSE_SUPER_JOB_LABEL='le-chonk-🐱'
export DENSE_SUPER_SCHEDULER=packed DENSE_SUPER_WITH_IMAGES=1 DENSE_INCLUDE_TEST=0
export DENSE_SUPER_ARTICLE_IDS='' DENSE_SUPER_EXPECTED='' DENSE_SUPER_BEAM_REPAIR=0 DENSE_SUPER_COMPETITION=0
export DENSE_SUPER_SPLITS=train
case "$mode" in
  smoke) export DENSE_SUPER_LIMIT_ARTICLES=2 DENSE_HOURS="${DENSE_HOURS:-8}";;
  pilot) export DENSE_SUPER_LIMIT_ARTICLES=48 DENSE_HOURS="${DENSE_HOURS:-24}";;
  overnight) export DENSE_SUPER_LIMIT_ARTICLES=24 DENSE_HOURS="${DENSE_HOURS:-12}";;
  long) export DENSE_SUPER_LIMIT_ARTICLES='' DENSE_SUPER_SPLITS='train validation' DENSE_HOURS="${DENSE_HOURS:-72}";;
  *) echo 'Usage: run_dense_le_chonk.sh [smoke|pilot|overnight|long] [new-work-directory]' >&2; exit 2;;
esac
printf 'Le Chonk 🐱: NVFP4, embedded DSpark3, one TP8 B200 worker, text + images.\n'
exec bash "$repo/scripts/run_dense_nemotron_super.sh" "$@"
