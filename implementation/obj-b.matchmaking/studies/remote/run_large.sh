#!/usr/bin/env bash
# Run the large matchmaking study on a Linux host.
#
# Large is 205 queues (hosted backend) and 100,000 tickets per scenario.
# Prefer tmux/screen. See remote/README.md for setup and packing results.
#
#   bash implementation/obj-b.matchmaking/studies/remote/run_large.sh
#   bash implementation/obj-b.matchmaking/studies/remote/run_large.sh --only base
set -euo pipefail

# shellcheck source=./_repo_root.sh
source "$(cd "$(dirname "$0")" && pwd)/_repo_root.sh"
SIZE_DIR="$STUDIES_ROOT/large"
LOG="$SIZE_DIR/remote-run.log"
mkdir -p "$SIZE_DIR"
cd "$ROOT"
{
  echo "==== $(date -Is) large start ===="
  python3 implementation/obj-b.matchmaking/studies/run_study.py --size large "$@"
  echo "==== $(date -Is) large done ===="
} 2>&1 | tee -a "$LOG"
