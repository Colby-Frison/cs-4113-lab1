#!/usr/bin/env python3
"""Monolithic matchmaking server.

One visit sleeps tasks 0 through 5 in order: validate, rating, route, search,
ready check, and allocate. The original ticket row is forwarded on the single
downstream edge. Splitting those waits across processes is the stage server.
"""

from __future__ import annotations

import sys
from pathlib import Path

MM_ROOT = Path(__file__).resolve().parents[1]
OBJ_A = MM_ROOT.parent / "obj-a"
sys.path.insert(0, str(MM_ROOT))
sys.path.insert(0, str(OBJ_A))

from mm.runtime import run_monolith


def main(argv: list[str] | None = None) -> int:
    return run_monolith(argv)


if __name__ == "__main__":
    raise SystemExit(main())
