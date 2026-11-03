#!/usr/bin/env bash
# One-shot: install user-local deps and run expand/hosted unit tests.
# Resolves the repo from this script's path (works on WSL, VirtualBox, remotes).
set -euo pipefail

# shellcheck source=./_repo_root.sh
source "$(cd "$(dirname "$0")" && pwd)/_repo_root.sh"
cd "$ROOT"
echo "cwd=$(pwd)"
python3 --version
export PATH="${HOME}/.local/bin:${PATH}"
if ! python3 -m pip --version >/dev/null 2>&1; then
  curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
  python3 /tmp/get-pip.py --user --break-system-packages
fi
python3 -m pip install --user --break-system-packages -q \
  -r implementation/obj-a/requirements.txt matplotlib pandas
cd implementation/obj-b.matchmaking/tests
python3 -m unittest test_expand.py test_hosted.py
echo "bootstrap ok"
