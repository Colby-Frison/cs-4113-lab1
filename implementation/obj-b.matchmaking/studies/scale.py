"""Scale search workers the way the Objective A sanity check scales a pool.

The total follows the factor. A floor that would make two factors identical
on every queue still leaves different totals, because the leftover workers
are handed out one queue at a time. Stages named in the pattern's
``swept_stages`` move together. The others stay at their pattern count.
"""

from __future__ import annotations

import math
from dataclasses import replace

from .expand import Layout, QueueSpec

# Name, factor. The seven sets from the lab slides.
SCENARIOS: tuple[tuple[str, float], ...] = (
    ("base", 1.0),
    ("srv-10", 0.9),
    ("srv-20", 0.8),
    ("srv-30", 0.7),
    ("srv+25", 1.25),
    ("srv+50", 1.5),
    ("srv+100", 2.0),
)


def scale_servers(baseline: dict[int, int], factor: float) -> dict[int, int]:
    """Floor each count, keep at least one, then nudge the total to ``factor`` times baseline."""
    if factor == 1.0:
        return dict(baseline)
    raw = {index: count * factor for index, count in baseline.items()}
    out = {index: max(1, math.floor(value)) for index, value in raw.items()}
    target = max(len(baseline), round(sum(baseline.values()) * factor))
    diff = target - sum(out.values())
    if diff > 0:
        order = sorted(raw, key=lambda index: raw[index] - math.floor(raw[index]), reverse=True)
        for index in order[:diff]:
            out[index] += 1
    elif diff < 0:
        order = sorted(
            (index for index in out if out[index] > 1),
            key=lambda index: raw[index] - out[index],
        )
        for index in order[: -diff]:
            out[index] -= 1
    return out


def scale_layout(layout: Layout, factor: float) -> Layout:
    """Return a layout whose swept stages follow ``factor``. Other stages are copied."""
    scaled: dict[int, int] = {}
    for stage in layout.swept_stages:
        baseline = {queue.number: queue.workers for queue in layout.queues if queue.stage == stage}
        scaled.update(scale_servers(baseline, factor))
    queues: list[QueueSpec] = []
    for queue in layout.queues:
        if queue.number in scaled:
            queues.append(replace(queue, workers=scaled[queue.number]))
        else:
            queues.append(queue)
    return replace(layout, queues=tuple(queues))


def worker_span(layout: Layout) -> str:
    """``4`` when every search queue matches, otherwise ``3–4``."""
    counts = [queue.workers for queue in layout.search_queues]
    low, high = min(counts), max(counts)
    if low == high:
        return str(low)
    return f"{low}–{high}"
