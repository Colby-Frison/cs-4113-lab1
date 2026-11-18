#!/usr/bin/env python3
"""Monolithic router server function.

One visit does a router's whole job: validate the packet, look up the next hop,
reduce the TTL, and deliver or forward. See the README for the order of events.
"""

from __future__ import annotations

import sys
from pathlib import Path

_APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_APP), str(_APP.parent / "obj-a")]

from pk.runtime import MONOLITH, run_cli


def main(argv: list[str] | None = None) -> int:
    return run_cli(MONOLITH, argv, "Monolithic router server function.")


if __name__ == "__main__":
    raise SystemExit(main())
