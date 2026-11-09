"""Stable hash routing for KV sharding.

``shard_index(key, n)`` maps a key onto ``0 .. n-1`` the same way in every
process (``zlib.crc32``, not Python's salted ``hash``). Use it to pick a file
or gRPC edge, or to split a job CSV into per-shard inputs.
"""

from __future__ import annotations

import random
import zlib
from typing import Callable

from lib.grpc_spec import DialSpec
from lib.job import Job


def shard_index(key: str, n: int) -> int:
    """Return the shard for ``key`` in ``0 .. n-1``."""
    if n < 1:
        raise ValueError("n must be >= 1")
    digest = zlib.crc32(key.encode("utf-8")) & 0xFFFFFFFF
    return digest % n


def partition_lines(lines: list[str], n: int, *, key_from_line: Callable[[str], str]) -> list[list[str]]:
    """Split job lines (no header) into ``n`` buckets by ``shard_index``."""
    if n < 1:
        raise ValueError("n must be >= 1")
    buckets: list[list[str]] = [[] for _ in range(n)]
    for line in lines:
        buckets[shard_index(key_from_line(line), n)].append(line)
    return buckets


def make_shard_edge_chooser(n: int) -> Callable[[Job, list[str], list[DialSpec], random.Random], str | None]:
    """Pick outfile or dial edge ``shard_index(job_id, n)`` among the open edges.

    ``outfiles`` and ``grpc_edges`` are concatenated in that order as the edge
    list of length ``n``. Raises at call time if the open edge count is not ``n``.
    """
    if n < 1:
        raise ValueError("n must be >= 1")

    def choose(
        job: Job,
        outfiles: list[str],
        grpc_edges: list[DialSpec],
        _rng: random.Random,
    ) -> str | None:
        edges: list[str] = list(outfiles) + [edge.address for edge in grpc_edges]
        if len(edges) != n:
            raise ValueError(f"expected {n} shard edges, got {len(edges)}")
        return edges[shard_index(job.job_id, n)]

    return choose
