"""Lab 0 event lines for the wall-clock queue and server functions.

The message body matches the simulator log::

    {time} {job_id} IN {qid} {KIND} {detail}

``time`` is wall-clock seconds with three decimal places (one token), taken
from ``time.time()`` at the moment the helper is called. Callers log that
string with ``logger.info``. The process logger still adds its own prefix
(``asctime - component_pid@host - level - message``); later tools strip that
prefix and keep the body. ``edge_label`` keeps only the filename for a path,
so ``ARRIVE-VIA`` and ``DEPART-VIA`` stay short. A host:port has no slash, so
it is left unchanged.

Task completion uses Lab 0's name ``TASK-END``. The quoted wait token uses the
same six-decimal ``W`` text as the CSV task, so a log line and a job row name
the same sleep.
"""

from __future__ import annotations

import os
import time

from lib.job import format_wait


def format_timestamp(now: float | None = None) -> str:
    """Wall-clock seconds, three decimal places, no spaces."""
    if now is None:
        now = time.time()
    return f"{now:.3f}"


def edge_label(path: str) -> str:
    """Basename used in ARRIVE-VIA and DEPART-VIA, matching Lab 0."""
    name = os.path.basename(path)
    return name or path


def format_event(
    job_id: str,
    qid: str,
    kind: str,
    detail: str | None = None,
    now: float | None = None,
) -> str:
    """Build one event message. ``detail`` is omitted when empty."""
    head = f"{format_timestamp(now)} {job_id} IN {qid} {kind}"
    if detail:
        return f"{head} {detail}"
    return head


def arrive_via(job_id: str, qid: str, source: str, now: float | None = None) -> str:
    """Job accepted from ``source`` (file or FIFO path)."""
    return format_event(job_id, qid, "ARRIVE-VIA", edge_label(source), now)


def depart_via(job_id: str, qid: str, dest: str, now: float | None = None) -> str:
    """Job handed to ``dest`` (outfile basename)."""
    return format_event(job_id, qid, "DEPART-VIA", edge_label(dest), now)


def enters_server(job_id: str, qid: str, server_id: int, now: float | None = None) -> str:
    """Server function started a visit."""
    return format_event(job_id, qid, "ENTERS-SERVER", str(server_id), now)


def exits_server(job_id: str, qid: str, server_id: int, now: float | None = None) -> str:
    """Server function finished a visit."""
    return format_event(job_id, qid, "EXITS-SERVER", str(server_id), now)


def task_start(
    job_id: str,
    qid: str,
    tid: int,
    wait_seconds: float,
    now: float | None = None,
    *, wait_precision: int = 6,
) -> str:
    """Sleep for this task is about to start."""
    return format_event(job_id, qid, "TASK-START", f'{tid} "{format_wait(wait_seconds, precision=wait_precision)}"', now)


def task_end(
    job_id: str,
    qid: str,
    tid: int,
    wait_seconds: float,
    now: float | None = None,
    *, wait_precision: int = 6,
) -> str:
    """Sleep for this task finished. Lab 0 calls this TASK-END."""
    return format_event(job_id, qid, "TASK-END", f'{tid} "{format_wait(wait_seconds, precision=wait_precision)}"', now)


def error_queue_full(job_id: str, qid: str, now: float | None = None) -> str:
    """Buffer was at ``--qsize``; the job was dropped."""
    return format_event(job_id, qid, "ERROR", "QUEUE-FULL", now)


def error_ttl_expired(job_id: str, qid: str, now: float | None = None) -> str:
    """The packet's TTL ran out before it reached its destination."""
    return format_event(job_id, qid, "ERROR", "TTL-EXPIRED", now)


def error_no_out_edge(job_id: str, qid: str, now: float | None = None) -> str:
    """Visit finished and the server function had no outfile."""
    return format_event(job_id, qid, "ERROR", "NO-OUT-EDGE", now)
