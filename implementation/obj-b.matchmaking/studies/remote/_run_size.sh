#!/usr/bin/env bash
# Generic size runner used during local WSL debugging. Prefer run_medium.sh /
# run_large.sh / run_very_large.sh on remotes (they tee to remote-run.log).
set -euo pipefail

SIZE="${1:?size required}"
shift || true

# shellcheck source=./_repo_root.sh
source "$(cd "$(dirname "$0")" && pwd)/_repo_root.sh"
export PATH="${HOME}/.local/bin:${PATH}"
cd "$ROOT"
echo "==== $(date -Is) $SIZE start (ROOT=$ROOT) ===="
python3 implementation/obj-b.matchmaking/studies/run_study.py --size "$SIZE" "$@"
echo "==== $(date -Is) $SIZE done ===="
