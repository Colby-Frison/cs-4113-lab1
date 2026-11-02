#!/usr/bin/env bash
# Run the very-large matchmaking study on a Linux host.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
SIZE_DIR="$ROOT/implementation/obj-b.matchmaking/studies/very-large"
LOG="$SIZE_DIR/remote-run.log"
mkdir -p "$SIZE_DIR"
cd "$ROOT"
{
  echo "==== $(date -Is) very-large start ===="
  python3 implementation/obj-b.matchmaking/studies/run_study.py --size very-large "$@"
  echo "==== $(date -Is) very-large done ===="
} 2>&1 | tee -a "$LOG"
