#!/usr/bin/env python3
"""Run the monolith file demo, then the six-stage file demo."""

from __future__ import annotations

import sys
from pathlib import Path

MM_ROOT = Path(__file__).resolve().parent
OBJ_A = MM_ROOT.parent / "obj-a"
sys.path.insert(0, str(MM_ROOT))
sys.path.insert(0, str(OBJ_A))

from mm.demo import main

if __name__ == "__main__":
    raise SystemExit(main())
