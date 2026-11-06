#!/usr/bin/env bash
# Local WSL entry for the Medium matchmaking study (full seven sets).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=./_repo_root.sh
source "${SCRIPT_DIR}/_repo_root.sh"
cd "$ROOT"

echo "ROOT=$ROOT"
python3 -c "import grpc, matplotlib, pandas; print('deps_ok')"

LOG="${STUDIES_ROOT}/medium/remote-run.log"
mkdir -p "${STUDIES_ROOT}/medium"
echo "==== medium local start $(date -Is) ====" | tee -a "$LOG"

# Full Medium sweep; forward any extra args (e.g. --only base).
set +e
python3 implementation/obj-b.matchmaking/studies/run_study.py --size medium "$@" 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
set -e

echo "==== medium local end $(date -Is) rc=$rc ====" | tee -a "$LOG"
exit "$rc"
