#!/usr/bin/env bash
# Resolve the lab repository root from this file's location.
# remote/ -> studies/ -> obj-b.matchmaking/ -> implementation/ -> <repo>
#
# Source from any script in this directory:
#   # shellcheck source=./_repo_root.sh
#   source "$(cd "$(dirname "$0")" && pwd)/_repo_root.sh"
# After sourcing, ROOT is an absolute path to the repo and STUDIES_ROOT points
# at implementation/obj-b.matchmaking/studies.
REMOTE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$REMOTE_DIR/../../../.." && pwd)"
STUDIES_ROOT="$ROOT/implementation/obj-b.matchmaking/studies"
