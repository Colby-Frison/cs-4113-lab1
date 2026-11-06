#!/usr/bin/env bash
# Resume Large after a SIGTERM mid-postprocess: replot base from runs/, then
# execute the remaining six scenarios.
set -euo pipefail

# shellcheck source=./_repo_root.sh
source "$(cd "$(dirname "$0")" && pwd)/_repo_root.sh"
SIZE_DIR="$STUDIES_ROOT/large"
LOG="$SIZE_DIR/remote-run.log"
mkdir -p "$SIZE_DIR"
cd "$ROOT"

{
  echo "==== $(date -Is) large resume: plots-only base ===="
  python3 implementation/obj-b.matchmaking/studies/run_study.py --size large --only base --plots-only
  echo "==== $(date -Is) large resume: remaining scenarios ===="
  python3 implementation/obj-b.matchmaking/studies/run_study.py --size large \
    --only 'srv-10,srv-20,srv-30,srv+25,srv+50,srv+100'
  echo "==== $(date -Is) large resume done ===="
} 2>&1 | tee -a "$LOG"
