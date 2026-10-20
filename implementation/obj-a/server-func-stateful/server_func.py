#!/usr/bin/env python3
"""Stateful wait-task server function.

Same wait-and-forward visit as the stateless server. This process also keeps
a visit count per job id. The count is logged as ``state-visits`` when the
input reaches EOF. It is not written on the outgoing edge, so the next queue
still receives the original job line.

The dictionary lives only in this process.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.server import run_cli


def main(argv: list[str] | None = None) -> int:
    return run_cli(argv, stateful=True)


if __name__ == "__main__":
    raise SystemExit(main())
