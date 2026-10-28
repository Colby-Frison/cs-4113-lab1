#!/usr/bin/env python3
"""Generate party-ticket rows for the ranked matchmaking pipeline.

Each job is one party. The job id carries region, mode, skill band, and
party size. The six tasks are the stage waits, already multiplied by
``--time-scale``. Interarrival is exponential. See the README for the mixes
and the service draws.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

MM_ROOT = Path(__file__).resolve().parents[1]
OBJ_A = MM_ROOT.parent / "obj-a"
sys.path.insert(0, str(MM_ROOT))
sys.path.insert(0, str(OBJ_A))

from mm.model import DEFAULT_ARRIVAL_MEAN, DEFAULT_TIME_SCALE
from mm.ticket import MODES, REGIONS
from mm.workload import WorkloadConfig, generate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate ranked matchmaking party tickets.")
    parser.add_argument("--out", help="Output CSV path (default: stdout).")
    parser.add_argument("--count", type=int, default=None, help="Stop after this many tickets.")
    parser.add_argument("--duration", type=float, default=None, help="Stop after this many seconds.")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--time-scale",
        type=float,
        default=DEFAULT_TIME_SCALE,
        help=f"Multiply every draw, including interarrival (default: {DEFAULT_TIME_SCALE}).",
    )
    parser.add_argument(
        "--arrival-mean",
        type=float,
        default=DEFAULT_ARRIVAL_MEAN,
        help=f"Unscaled exponential interarrival mean in seconds (default: {DEFAULT_ARRIVAL_MEAN}).",
    )
    parser.add_argument("--region", choices=REGIONS, help="Pin every ticket to this region.")
    parser.add_argument("--mode", choices=MODES, help="Pin every ticket to this mode.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = WorkloadConfig(
        time_scale=args.time_scale,
        arrival_mean=args.arrival_mean,
        region=args.region,
        mode=args.mode,
        count=args.count,
        duration=args.duration,
        seed=args.seed,
    )
    owned = args.out is not None
    out = open(args.out, "w", encoding="utf-8", newline="") if owned else sys.stdout
    try:
        try:
            generate(config, out)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        except KeyboardInterrupt:
            return 0
    finally:
        if owned:
            out.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
