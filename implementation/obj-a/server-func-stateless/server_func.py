#!/usr/bin/env python3
"""Stateless wait-task server function.

Each visit sleeps the job's wait tasks in order, logs the Lab 0 server
events, and forwards the original job line on one randomly chosen outfile.
No visit state is kept.

gRPC flags are rejected. See the README for a FIFO startup example.
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
