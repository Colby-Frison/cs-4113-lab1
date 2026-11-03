#!/usr/bin/env bash
# Run the medium matchmaking study on a Linux host.
#
# Medium is 45 queues (process backend) and 10,000 tickets per scenario.
# The single ready worker is past capacity at the tuned arrival, so a full
# seven-scenario sweep can take well over an hour. Prefer tmux/screen.
#
# Usage:
#   bash implementation/obj-b.matchmaking/studies/remote/run_medium.sh
#   bash implementation/obj-b.matchmaking/studies/remote/run_medium.sh --only base
set -euo pipefail

# shellcheck source=./_repo_root.sh
source "$(cd "$(dirname "$0")" && pwd)/_repo_root.sh"
SIZE_DIR="$STUDIES_ROOT/medium"
LOG="$SIZE_DIR/remote-run.log"
mkdir -p "$SIZE_DIR"
cd "$ROOT"
{
  echo "==== $(date -Is) medium start ===="
  python3 implementation/obj-b.matchmaking/studies/run_study.py --size medium "$@"
  echo "==== $(date -Is) medium done ===="
} 2>&1 | tee -a "$LOG"
