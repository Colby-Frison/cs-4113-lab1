#!/usr/bin/env bash
# Thin wrapper: run any size through run_study.py from a Linux shell on this host.
# For long Medium/Large/Very Large sweeps prefer the dedicated run_*.sh scripts
# (they tee to studies/<size>/remote-run.log). See remote/README.md.
set -euo pipefail

SIZE="${1:?usage: $0 <small|medium|large|very-large> [run_study args...]}"
shift || true

# shellcheck source=./_repo_root.sh
source "$(cd "$(dirname "$0")" && pwd)/_repo_root.sh"
cd "$ROOT"
exec python3 implementation/obj-b.matchmaking/studies/run_study.py --size "$SIZE" "$@"
