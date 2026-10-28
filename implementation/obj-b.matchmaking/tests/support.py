"""Put the matchmaking tree and Objective A on sys.path."""

from __future__ import annotations

import sys
from pathlib import Path

MM_ROOT = Path(__file__).resolve().parents[1]
OBJ_A = MM_ROOT.parent / "obj-a"
for entry in (MM_ROOT, OBJ_A):
    text = str(entry)
    if text not in sys.path:
        sys.path.insert(0, text)
