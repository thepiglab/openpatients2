#!/usr/bin/env bash
# Beam only. Freeze a matched speculation experiment before the full campaign.
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
mode="${1:-pilot}"
if [[ $# -gt 0 ]]; then shift; fi
export DENSE_SUPER_SCHEDULER=packed DENSE_SUPER_WITH_IMAGES=0 DENSE_INCLUDE_TEST=0
export DENSE_SUPER_ARTICLE_IDS='' DENSE_SUPER_EXPECTED=''
export DENSE_SUPER_COMPETITION=0
export DENSE_SUPER_BEAM_REPAIR="${DENSE_BEAM_REPAIR:-0}"
case "$mode" in
  competition|overnight)
    export DENSE_SUPER_COMPETITION=1 DENSE_SUPER_BEAM_REPAIR=1
    export DENSE_SUPER_PROFILES='beam-nvfp4-dflash2 beam-nvfp4-no-spec'
    export DENSE_SUPER_SPLITS=train DENSE_SUPER_LIMIT_ARTICLES=''
    export DENSE_HOURS="${DENSE_HOURS:-12}"
    ;;
  diagnostic)
    export DENSE_SUPER_BEAM_REPAIR=1
    export DENSE_SUPER_PROFILES=beam-nvfp4-dflash2
    export DENSE_SUPER_SPLITS=train DENSE_SUPER_LIMIT_ARTICLES=''
    # Fixed training-only failures + representative cases + empty controls.
    export DENSE_SUPER_ARTICLE_IDS='PMC12283752.1 PMC12373494.1 PMC12486612.1 PMC12779367.1 PMC12780777.1 PMC12834360.1 PMC13476113.1 PMC7770219.1 PMC13575834.1 PMC12373261.1 PMC12835657.1 PMC12470713.1'
    export DENSE_HOURS="${DENSE_HOURS:-12}"
    ;;
  repair-pilot)
    export DENSE_SUPER_BEAM_REPAIR=1
    export DENSE_SUPER_PROFILES=beam-nvfp4-dflash2
    export DENSE_SUPER_SPLITS=train DENSE_SUPER_LIMIT_ARTICLES="${DENSE_BEAM_PILOT_ARTICLES:-48}"
    export DENSE_HOURS="${DENSE_HOURS:-24}"
    ;;
  pilot|short-draft)
    speculative=beam-nvfp4-dflash2
    if [[ "$mode" == short-draft ]]; then speculative=beam-nvfp4-dflash2-short; fi
    export DENSE_SUPER_PROFILES="$speculative beam-nvfp4-no-spec"
    export DENSE_SUPER_SPLITS=train DENSE_SUPER_LIMIT_ARTICLES="${DENSE_BEAM_PILOT_ARTICLES:-48}"
    export DENSE_HOURS="${DENSE_HOURS:-24}"
    ;;
  long)
    export DENSE_SUPER_BEAM_REPAIR="${DENSE_BEAM_REPAIR:-1}"
    profile="${DENSE_BEAM_PROFILE:-beam-nvfp4-dflash2}"
    case "$profile" in
      beam-nvfp4-dflash2|beam-nvfp4-no-spec|beam-nvfp4-dflash2-short) ;;
      *) echo 'Choose a supported Beam NVFP4 profile in DENSE_BEAM_PROFILE.' >&2; exit 2;;
    esac
    export DENSE_SUPER_PROFILES="$profile" DENSE_SUPER_SPLITS='train validation'
    export DENSE_SUPER_LIMIT_ARTICLES='' DENSE_HOURS="${DENSE_HOURS:-72}"
    ;;
  *) echo 'Usage: run_dense_beam.sh [overnight|competition|diagnostic|repair-pilot|pilot|short-draft|long] [new-work-directory]' >&2; exit 2;;
esac
exec bash "$repo/scripts/run_dense_nemotron_super.sh" "$@"
