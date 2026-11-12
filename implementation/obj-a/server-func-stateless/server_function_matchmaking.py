#!/usr/bin/env python3
"""One matchmaking stage.

``--stage`` selects which task id is slept. The original ticket row is
forwarded. Route picks the edge from the region token. Ready check passes,
returns the ticket to search, or sends it to the failed sink once the visit
cap is reached. The other stages have one downstream edge.
"""

from __future__ import annotations

import sys
from pathlib import Path

MM_ROOT = Path(__file__).resolve().parents[1]
OBJ_A = MM_ROOT.parent / "obj-a"
sys.path.insert(0, str(MM_ROOT))
sys.path.insert(0, str(OBJ_A))

from mm.runtime import run_stage


def main(argv: list[str] | None = None) -> int:
    return run_stage(argv)


if __name__ == "__main__":
    raise SystemExit(main())
