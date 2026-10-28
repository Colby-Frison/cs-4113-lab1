"""Stream party-ticket jobs until a count, a duration, or Ctrl-C.

The loop writes the CSV header and flushes, then repeats: draw an exponential
interarrival, sleep it, draw the six stage waits, write one row, flush. The
sleep happens before every job, including the first. ``--count`` is checked
at the top of the loop, so it wins when both limits are set. A duration that
runs out during the arrival sleep returns before the row is written.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, TextIO

from lib.job import HEADER, format_job_line

from mm.model import (
    DEFAULT_ARRIVAL_MEAN,
    DEFAULT_TIME_SCALE,
    MODE_WEIGHTS,
    PARTY_WEIGHTS,
    REGION_WEIGHTS,
    band_for_skill,
    check_token,
    draw_exponential,
    draw_tasks,
    weighted_choice,
)
from mm.ticket import MODES, REGIONS, format_ticket_id

SleepFn = Callable[[float], None]
ClockFn = Callable[[], float]


@dataclass
class WorkloadConfig:
    """Knobs for one generator run. Times on the flags are unscaled seconds."""

    time_scale: float = DEFAULT_TIME_SCALE
    arrival_mean: float = DEFAULT_ARRIVAL_MEAN
    region: str | None = None
    mode: str | None = None
    count: int | None = None
    duration: float | None = None
    seed: int | None = None


def validate_config(config: WorkloadConfig) -> None:
    if config.time_scale < 0:
        raise ValueError("--time-scale must be >= 0")
    if config.arrival_mean < 0:
        raise ValueError("--arrival-mean must be >= 0")
    if config.count is not None and config.count < 1:
        raise ValueError("--count must be >= 1")
    if config.duration is not None and config.duration <= 0:
        raise ValueError("--duration must be > 0")
    if config.region is not None:
        check_token("--region", config.region, REGIONS)
    if config.mode is not None:
        check_token("--mode", config.mode, MODES)


def generate(
    config: WorkloadConfig,
    out: TextIO,
    rng: random.Random | None = None,
    *,
    sleep_fn: SleepFn = time.sleep,
    now_fn: ClockFn = time.time,
    monotonic_fn: ClockFn = time.monotonic,
) -> int:
    """Write the header and jobs. Return how many jobs were emitted."""
    validate_config(config)
    source = rng or random.Random(config.seed)
    deadline = None if config.duration is None else monotonic_fn() + config.duration
    arrival_mean = config.arrival_mean * config.time_scale

    out.write(HEADER + "\n")
    out.flush()

    emitted = 0
    while True:
        if config.count is not None and emitted >= config.count:
            break
        delay = draw_exponential(source, arrival_mean)
        if not _sleep_before_job(delay, deadline, sleep_fn, monotonic_fn):
            break
        region = config.region or str(weighted_choice(source, REGION_WEIGHTS))
        mode = config.mode or str(weighted_choice(source, MODE_WEIGHTS))
        party = int(weighted_choice(source, PARTY_WEIGHTS))
        band = band_for_skill(source.gauss(0.0, 1.0))
        tasks = draw_tasks(source, band, party, config.time_scale)
        job_id = format_ticket_id(emitted, region, mode, band, party)
        out.write(format_job_line(job_id, now_fn(), tasks) + "\n")
        out.flush()
        emitted += 1
    return emitted


def _sleep_before_job(
    delay: float,
    deadline: float | None,
    sleep_fn: SleepFn,
    monotonic_fn: ClockFn,
) -> bool:
    """Sleep up to ``delay`` seconds. False means the duration limit won."""
    if delay < 0:
        delay = 0.0
    if deadline is None:
        sleep_fn(delay)
        return True
    remaining = deadline - monotonic_fn()
    if remaining <= 0 or delay >= remaining:
        if remaining > 0:
            sleep_fn(remaining)
        return False
    sleep_fn(delay)
    return monotonic_fn() < deadline
