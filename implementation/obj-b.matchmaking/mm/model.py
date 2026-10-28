"""Unscaled service draws for one party ticket.

Times in this module are matchmaking seconds. The generator multiplies every
draw, including the interarrival, by ``--time-scale``. A server sleeps the
``W`` value stored on the row and does not scale it again.

One search server and the default interarrival of 14 seconds put offered load
near 0.8. The mean search time is about 11.5 seconds once the band cuts and
the party mix are included. Search is the long stage.
"""

from __future__ import annotations

import math
import random
import sys

from lib.job import Task

SEARCH_MEAN = {
    "placement": 12.0,
    "low": 8.0,
    "mid": 6.0,
    "high": 14.0,
    "apex": 28.0,
}
PARTY_SEARCH_FACTOR = 1.4
ROUTE_WAIT = 0.02
READY_WAIT = 2.0
DEFAULT_TIME_SCALE = 0.05
DEFAULT_ARRIVAL_MEAN = 14.0

REGION_WEIGHTS = (("na", 0.40), ("eu", 0.30), ("ap", 0.20), ("sa", 0.10))
MODE_WEIGHTS = (("ranked", 0.70), ("unrated", 0.30))
PARTY_WEIGHTS = ((1, 0.55), (2, 0.25), (3, 0.12), (4, 0.05), (5, 0.03))

# Standard-normal cuts. Apex is the upper tail.
_BAND_CUTS = ((-1.0, "placement"), (-0.2, "low"), (0.6, "mid"), (1.5, "high"))


def search_mean(band: str, party: int) -> float:
    """Unscaled exponential mean for the search task."""
    if band not in SEARCH_MEAN:
        raise ValueError(f"unknown band {band}")
    if party < 1 or party > 5:
        raise ValueError("party must be 1..5")
    mean = SEARCH_MEAN[band]
    if party > 2:
        return mean * PARTY_SEARCH_FACTOR
    return mean


def band_for_skill(z: float) -> str:
    """Map a standard-normal skill draw onto a band."""
    for cut, name in _BAND_CUTS:
        if z < cut:
            return name
    return "apex"


def draw_exponential(rng: random.Random, mean: float) -> float:
    """Exponential draw. A non-positive mean returns 0."""
    if mean <= 0:
        return 0.0
    unit = 1.0 - rng.random()
    if unit <= 0.0:
        unit = sys.float_info.min
    return -mean * math.log(unit)


def weighted_choice(rng: random.Random, pairs: tuple[tuple[object, float], ...] | list) -> object:
    """Pick a value with probability proportional to its weight."""
    total = sum(weight for _value, weight in pairs)
    if total <= 0:
        raise ValueError("weights must sum to a positive number")
    pick = rng.random() * total
    running = 0.0
    for value, weight in pairs:
        running += weight
        if pick <= running:
            return value
    return pairs[-1][0]


def draw_tasks(rng: random.Random, band: str, party: int, time_scale: float) -> tuple[Task, ...]:
    """Six waits, tids 0 through 5, already multiplied by ``time_scale``."""
    scale = time_scale
    validate = rng.uniform(0.05, 0.15) * scale
    if rng.random() < 0.90:
        rating = rng.uniform(0.05, 0.15) * scale
    else:
        rating = rng.uniform(0.40, 0.80) * scale
    route = ROUTE_WAIT * scale
    search = draw_exponential(rng, search_mean(band, party)) * scale
    ready = READY_WAIT * scale
    allocate = rng.uniform(0.40, 0.80) * scale
    waits = (validate, rating, route, search, ready, allocate)
    return tuple(Task(tid, wait) for tid, wait in enumerate(waits))


def check_token(name: str, value: str, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise ValueError(f"{name} must be one of {', '.join(allowed)}")


