#!/usr/bin/env bash
# Run the large matchmaking study on a Linux host.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
SIZE_DIR="$ROOT/implementation/obj-b.matchmaking/studies/large"
LOG="$SIZE_DIR/remote-run.log"
mkdir -p "$SIZE_DIR"
cd "$ROOT"
{
  echo "==== $(date -Is) large start ===="
  python3 implementation/obj-b.matchmaking/studies/run_study.py --size large "$@"
  echo "==== $(date -Is) large done ===="
} 2>&1 | tee -a "$LOG"
