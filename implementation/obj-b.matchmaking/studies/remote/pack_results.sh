#!/usr/bin/env bash
# Pack PNGs and summary.md for one size so they can be copied off a remote host.
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <large|very-large>" >&2
  exit 2
fi

SIZE="$1"
case "$SIZE" in
  large|very-large) ;;
  *)
    echo "size must be large or very-large" >&2
    exit 2
    ;;
esac

ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
SIZE_DIR="$ROOT/implementation/obj-b.matchmaking/studies/$SIZE"
RESULTS="$SIZE_DIR/results"
OUT="$SIZE_DIR/${SIZE}-results.tar.gz"

if [[ ! -f "$RESULTS/summary.md" ]]; then
  echo "missing $RESULTS/summary.md" >&2
  exit 1
fi

cd "$SIZE_DIR"
mapfile -t FILES < <(
  find results -type f \( -name '*.png' -o -name 'summary.md' \) | sort
)
if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "no PNGs or summary.md under $RESULTS" >&2
  exit 1
fi

tar -czf "$OUT" "${FILES[@]}"
echo "wrote $OUT (${#FILES[@]} files)"
