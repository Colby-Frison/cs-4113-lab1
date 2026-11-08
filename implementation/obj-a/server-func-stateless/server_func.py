#!/usr/bin/env python3
"""Stateless wait-task server function.

Each visit sleeps the job's wait tasks in order, logs the Lab 0 server
events, and forwards the original job line on one randomly chosen edge.
An edge is an outfile or an ``--outgrpc`` dial. No visit state is kept.
The input is one file or one ``--ingrpc`` listen port. On the listen path
the visit runs inside ``Submit`` before the RPC returns.

See the README for how a visit is ordered.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.server import run_cli


def main(argv: list[str] | None = None) -> int:
    return run_cli(argv, stateful=False)


if __name__ == "__main__":
    raise SystemExit(main())
