#!/usr/bin/env python3
"""Packet stream for one source router.

Each packet gets an exponential gap, a uniformly chosen destination router, a
random host inside its prefix, a size from the IMIX mix, and three waits. Rows
are flushed one at a time, so a reader on a FIFO sees the real spacing. See the
README for why each distribution was chosen.

Gaps and packet attributes use separate random streams, so changing the arrival
rate keeps the same sequence of packets.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path
from typing import Callable, TextIO

_APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_APP), str(_APP.parent / "obj-a")]

from lib.job import PACKET_HEADER, Task, format_job_line

from pk.router import FORWARD_TID, LOOKUP_TID, VALIDATE_TID
from pk.routing import router_network

# Simple IMIX: 7 small, 4 medium, and 1 large packet, in bytes.
IMIX_SIZES = (40, 576, 1500)
IMIX_WEIGHTS = (7, 4, 1)
IMIX_MEAN_BYTES = sum(s * w for s, w in zip(IMIX_SIZES, IMIX_WEIGHTS)) / sum(IMIX_WEIGHTS)


def positive(text: str) -> float:
    value = float(text)
    if not value > 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return value


def non_negative(text: str) -> float:
    value = float(text)
    if not value >= 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workload-generator", description=__doc__.split("\n")[0])
    parser.add_argument("--source", type=int, required=True, help="Index of this source router")
    parser.add_argument("--routers", type=int, required=True, help="Routers in the network")
    parser.add_argument("--rate", type=positive, required=True, help="Packets per second")
    parser.add_argument("--count", type=int, help="Stop after this many packets")
    parser.add_argument("--duration", type=positive, help="Stop after this many seconds")
    parser.add_argument("--ttl", type=int, default=64)
    parser.add_argument("--validate-wait", type=non_negative, default=0.005)
    parser.add_argument("--lookup-wait", type=non_negative, default=0.015)
    parser.add_argument(
        "--forward-wait-mean",
        type=non_negative,
        default=0.03,
        help="Mean forward wait. A packet's wait scales with its size.",
    )
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", help="File or FIFO (default: stdout)")
    parser.add_argument("--copy", help="Also write every row to this file")
    return parser


def packet_tasks(
    size: int, validate_wait: float, lookup_wait: float, forward_wait_mean: float
) -> tuple[Task, ...]:
    return (
        Task(VALIDATE_TID, validate_wait),
        Task(LOOKUP_TID, lookup_wait),
        Task(FORWARD_TID, forward_wait_mean * size / IMIX_MEAN_BYTES),
    )


def draw_destination(attributes: random.Random, source: int, routers: int) -> int:
    """A uniform router other than the source, and a host from 1 to 254 inside it."""
    router = attributes.randrange(routers - 1)
    if router >= source:
        router += 1
    return router_network(router) | attributes.randint(1, 254)


def generate(
    outputs: list[TextIO],
    args: argparse.Namespace,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> int:
    """Write rows until a limit is reached. Returns the number of packets."""
    gaps = random.Random(f"{args.seed}:{args.source}:gaps")
    attributes = random.Random(f"{args.seed}:{args.source}:attributes")
    deadline = clock() + args.duration if args.duration is not None else None

    def write(line: str) -> None:
        for output in outputs:
            output.write(line + "\n")
            output.flush()

    write(PACKET_HEADER)
    sequence = 0
    while args.count is None or sequence < args.count:
        gap = gaps.expovariate(args.rate)
        if deadline is not None and clock() + gap >= deadline:
            break
        sleep(gap)
        size = attributes.choices(IMIX_SIZES, IMIX_WEIGHTS)[0]
        write(
            format_job_line(
                f"p{args.source}-{sequence}",
                clock(),
                packet_tasks(size, args.validate_wait, args.lookup_wait, args.forward_wait_mean),
                dst_ip=draw_destination(attributes, args.source, args.routers),
                ttl=args.ttl,
            )
        )
        sequence += 1
    return sequence


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.routers < 2:
        parser.error("--routers must be at least 2")
    if not 0 <= args.source < args.routers:
        parser.error("--source must be a router index")
    if args.ttl < 1:
        parser.error("--ttl must be at least 1")
    if args.count is None and args.duration is None:
        parser.error("set --count or --duration")

    handles: list[TextIO] = []
    try:
        # The copy opens first, because opening a FIFO blocks until it has a reader.
        if args.copy:
            handles.append(open(args.copy, "w", encoding="utf-8", newline="\n"))
        handles.append(open(args.out, "w", encoding="utf-8", newline="\n") if args.out else sys.stdout)
        generate(handles, args)
    except KeyboardInterrupt:
        pass
    finally:
        for handle in handles:
            if handle is not sys.stdout:
                handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
