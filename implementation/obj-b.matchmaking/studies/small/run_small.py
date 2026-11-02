#!/usr/bin/env python3
"""Run the small study through the shared pattern runner.

``run_study.py --size small`` is the same command. This file keeps the path
the earlier notes used.
"""

from __future__ import annotations

import sys
from pathlib import Path

MM_ROOT = Path(__file__).resolve().parents[2]
if str(MM_ROOT) not in sys.path:
    sys.path.insert(0, str(MM_ROOT))

from studies.run_study import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(["--size", "small", *sys.argv[1:]]))
