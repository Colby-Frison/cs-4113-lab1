#!/usr/bin/env python3
"""Decomposed router server function.

One program, three stages. ``--stage`` selects the visit: validate, lookup, or
forward. Each stage runs behind its own queue. See the README for how a packet
crosses them.
"""

from __future__ import annotations

import sys
from pathlib import Path

_APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_APP), str(_APP.parent / "obj-a")]

from pk.runtime import run_cli


def main(argv: list[str] | None = None) -> int:
    return run_cli(None, argv, "Decomposed router server function.")


if __name__ == "__main__":
    raise SystemExit(main())
