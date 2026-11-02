"""Arrival that puts one baseline search shard near the target load.

Traffic is split across the search queues, so the single-queue interarrival
is divided by that count. Region weights are uneven, and some shards run hotter.
The same arrival leaves the single ready worker past capacity.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

MM_ROOT = Path(__file__).resolve().parents[1]
OBJ_A = MM_ROOT.parent / "obj-a"
for _entry in (str(MM_ROOT), str(OBJ_A)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from mm.model import (  # noqa: E402
    PARTY_SEARCH_FACTOR,
    PARTY_WEIGHTS,
    READY_WAIT,
    SEARCH_MEAN,
    _BAND_CUTS,
)


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def expected_search_mean() -> float:
    """Unscaled mean search wait, including the band cuts and the party factor."""
    names = [name for _cut, name in _BAND_CUTS] + ["apex"]
    edges = [-math.inf, *[cut for cut, _name in _BAND_CUTS], math.inf]
    band_mean = 0.0
    for index, name in enumerate(names):
        left = 0.0 if math.isinf(edges[index]) else _phi(edges[index])
        right = 1.0 if math.isinf(edges[index + 1]) else _phi(edges[index + 1])
        band_mean += (right - left) * SEARCH_MEAN[name]
    large = sum(weight for size, weight in PARTY_WEIGHTS if int(size) > 2)
    small = sum(weight for size, weight in PARTY_WEIGHTS if int(size) <= 2)
    return band_mean * ((small + large * PARTY_SEARCH_FACTOR) / (small + large))


def expected_search_visits(fail_p: float, max_visits: int) -> float:
    """Search visits per ticket. A failure below the cap comes back once more."""
    total = 0.0
    chance = 1.0
    for _ in range(max_visits):
        total += chance
        chance *= fail_p
    return total


def tuned_arrival_mean(
    search_queues: int,
    workers_per_queue: int,
    target_rho: float,
    fail_p: float,
    max_visits: int,
) -> float:
    """Unscaled interarrival so one baseline shard, including retries, sits near ``target_rho``.

    $\\rho = \\lambda E[S] E[V] / (c N)$ when arrivals split across $N$ search queues,
    so the mean interarrival is $E[S] E[V] / (\\rho c N)$.
    """
    if search_queues < 1 or workers_per_queue < 1 or target_rho <= 0:
        raise ValueError("search queues, workers, and rho must be positive")
    visits = expected_search_visits(fail_p, max_visits)
    offered = expected_search_mean() * visits
    return offered / (target_rho * workers_per_queue * search_queues)


def ready_rho(arrival_mean: float, fail_p: float, max_visits: int) -> float:
    """Offered load on the single ready worker at this unscaled interarrival."""
    if arrival_mean <= 0:
        return float("inf")
    visits = expected_search_visits(fail_p, max_visits)
    return (1.0 / arrival_mean) * visits * READY_WAIT
