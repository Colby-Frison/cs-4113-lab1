#!/usr/bin/env python3
"""Stream synthetic wait-task jobs until halted, or until a limit is reached.

The loop writes the CSV header and flushes, then repeats: draw an arrival
delay, sleep it, draw one wait per task, write one row, flush. The sleep
happens before every job, including the first, so the rate does not start
with a free job. ``--arrival-dist`` supplies that delay. ``--wait-dist`` is
drawn once per task and becomes the ``W`` seconds a server function will
sleep. ``--tasks`` is how many waits each job carries (default 1).

``fixed``, ``uniform``, and ``normal`` are fractional seconds. ``normal`` is
clamped at 0 with ``max(0, gauss(...))``. ``geometric`` counts failures before
the first success, so 0 seconds is possible. ``poisson`` is Knuth's sampler.
Both integer draws are used as whole seconds.

With neither ``--count`` nor ``--duration``, the loop runs until Ctrl-C,
which is caught and turned into exit code 0 after the file is closed. If both
limits are set, the check at the top of the loop stops on count, and a
duration that runs out during the arrival sleep returns before the row is
written.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from pathlib import Path
from typing import Callable, TextIO

# This file lives in a hyphenated directory, so the shared package is imported
# by putting implementation/obj-a on the path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.job import HEADER, Task, format_job_line

Draw = Callable[[], float]
DISTS = ("fixed", "uniform", "normal", "geometric", "poisson")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate wall-clock wait-task jobs as a Lab 0-shaped CSV."
    )
    _add_dist_flags(parser, "arrival")
    _add_dist_flags(parser, "wait")
    parser.add_argument(
        "--tasks",
        type=int,
        default=1,
        help="Waits per job (default: 1)",
    )
    parser.add_argument("--seed", type=int, default=None, help="RNG seed")
    parser.add_argument(
        "--out",
        default=None,
        help="Output CSV path (default: stdout)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Stop after this many jobs",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Stop after this many seconds of generation",
    )
    return parser


def _add_dist_flags(parser: argparse.ArgumentParser, prefix: str) -> None:
    parser.add_argument(
        f"--{prefix}-dist",
        required=True,
        choices=DISTS,
        help=f"Distribution for {prefix} seconds",
    )
    parser.add_argument(f"--{prefix}-fixed", type=float, help="Constant seconds")
    parser.add_argument(f"--{prefix}-low", type=float, help="Uniform low seconds")
    parser.add_argument(f"--{prefix}-high", type=float, help="Uniform high seconds")
    parser.add_argument(f"--{prefix}-mu", type=float, help="Normal mean seconds")
    parser.add_argument(f"--{prefix}-sigma", type=float, help="Normal stddev seconds")
    parser.add_argument(f"--{prefix}-p", type=float, help="Geometric success probability")
    parser.add_argument(f"--{prefix}-lam", type=float, help="Poisson lambda")


def validate_args(args: argparse.Namespace) -> None:
    if args.tasks < 1:
        raise SystemExit("--tasks must be >= 1")
    if args.count is not None and args.count < 1:
        raise SystemExit("--count must be >= 1")
    if args.duration is not None and args.duration <= 0:
        raise SystemExit("--duration must be > 0")
    _validate_dist(args, "arrival")
    _validate_dist(args, "wait")


def _validate_dist(args: argparse.Namespace, prefix: str) -> None:
    dist = getattr(args, f"{prefix}_dist")
    if dist == "fixed":
        value = getattr(args, f"{prefix}_fixed")
        if value is None or value < 0:
            raise SystemExit(f"--{prefix}-dist fixed requires --{prefix}-fixed >= 0")
    elif dist == "uniform":
        low = getattr(args, f"{prefix}_low")
        high = getattr(args, f"{prefix}_high")
        if low is None or high is None:
            raise SystemExit(f"--{prefix}-dist uniform requires --{prefix}-low and --{prefix}-high")
        if low < 0 or high < low:
            raise SystemExit(f"uniform requires 0 <= --{prefix}-low <= --{prefix}-high")
    elif dist == "normal":
        mu = getattr(args, f"{prefix}_mu")
        sigma = getattr(args, f"{prefix}_sigma")
        if mu is None or sigma is None:
            raise SystemExit(f"--{prefix}-dist normal requires --{prefix}-mu and --{prefix}-sigma")
        if sigma < 0:
            raise SystemExit(f"--{prefix}-sigma must be >= 0")
    elif dist == "geometric":
        p = getattr(args, f"{prefix}_p")
        if p is None or not (0 < p <= 1):
            raise SystemExit(f"--{prefix}-dist geometric requires --{prefix}-p in (0, 1]")
    elif dist == "poisson":
        lam = getattr(args, f"{prefix}_lam")
        if lam is None or lam < 0:
            raise SystemExit(f"--{prefix}-dist poisson requires --{prefix}-lam >= 0")


def make_draw(dist: str, stats: dict[str, float], rng: random.Random) -> Draw:
    """Return a sampler of seconds. Integer distributions are whole seconds."""
    if dist == "fixed":
        value = stats["fixed"]
        return lambda: value

    if dist == "uniform":
        low, high = stats["low"], stats["high"]
        return lambda: rng.uniform(low, high)

    if dist == "normal":
        mu, sigma = stats["mu"], stats["sigma"]

        def draw_normal() -> float:
            return max(0.0, rng.gauss(mu, sigma))

        return draw_normal

    if dist == "geometric":
        p = stats["p"]

        def draw_geometric() -> float:
            # Failures before the first success, so 0 seconds is possible.
            return float(_geometric(rng, p) - 1)

        return draw_geometric

    if dist == "poisson":
        lam = stats["lam"]
        return lambda: float(_poisson(rng, lam))

    raise ValueError(f"unknown distribution: {dist}")


def stats_from_args(args: argparse.Namespace, prefix: str) -> dict[str, float]:
    """Pull the populated numeric flags for one distribution."""
    keys = ("fixed", "low", "high", "mu", "sigma", "p", "lam")
    return {
        key: getattr(args, f"{prefix}_{key}")
        for key in keys
        if getattr(args, f"{prefix}_{key}") is not None
    }


def _geometric(rng: random.Random, p: float) -> int:
    """Trials until the first success (minimum 1), matching Lab 0."""
    trials = 1
    while rng.random() > p:
        trials += 1
    return trials


def _poisson(rng: random.Random, lam: float) -> int:
    """Knuth Poisson sampler, matching Lab 0."""
    if lam == 0:
        return 0
    threshold = pow(2.718281828459045, -lam)
    count = 0
    product = 1.0
    while True:
        count += 1
        product *= rng.random()
        if product <= threshold:
            return count - 1


def generate(args: argparse.Namespace, out: TextIO, rng: random.Random | None = None) -> int:
    """Write the header and jobs. Return how many jobs were emitted.

    Sleeps the arrival draw before every job. A duration deadline that fires
    during that sleep stops the stream without emitting a partial job.
    """
    source = rng or random.Random(args.seed)
    arrival_draw = make_draw(args.arrival_dist, stats_from_args(args, "arrival"), source)
    wait_draw = make_draw(args.wait_dist, stats_from_args(args, "wait"), source)
    deadline = None if args.duration is None else time.monotonic() + args.duration

    out.write(HEADER + "\n")
    out.flush()

    emitted = 0
    while True:
        if args.count is not None and emitted >= args.count:
            break
        delay = arrival_draw()
        if not _sleep_before_job(delay, deadline):
            break
        tasks = tuple(Task(tid, wait_draw()) for tid in range(args.tasks))
        line = format_job_line(f"j{emitted}", time.time(), tasks)
        out.write(line + "\n")
        out.flush()
        emitted += 1
    return emitted


def _sleep_before_job(delay: float, deadline: float | None) -> bool:
    """Sleep up to ``delay`` seconds. False means the duration limit won.

    When the arrival delay is longer than the time left, the rest of the
    budget is slept away and the job is not emitted. That decision does not
    depend on the sleep returning early.
    """
    if delay < 0:
        delay = 0.0
    if deadline is None:
        time.sleep(delay)
        return True
    remaining = deadline - time.monotonic()
    if remaining <= 0 or delay >= remaining:
        if remaining > 0:
            time.sleep(remaining)
        return False
    time.sleep(delay)
    return time.monotonic() < deadline


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(args)
    owned = args.out is not None
    out: TextIO
    if owned:
        out = open(args.out, "w", encoding="utf-8", newline="")
    else:
        out = sys.stdout
    try:
        try:
            generate(args, out)
        except KeyboardInterrupt:
            return 0
    finally:
        if owned:
            out.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
