"""Job records for the wall-clock wait workload.

A job is one CSV row with the Lab 0 columns ``Job Id``, ``arrival time``, and
``Tasks``. Each task is a wait in seconds, written ``<tid>: W <seconds>``.
``W`` is a sleep duration, not a discrete simulator work unit.

``format_job_line`` builds the row with the csv module so the Tasks JSON is
wrapped in doubled quotes, matching Lab 0. ``parse_job_line`` reverses that:
csv splits the three columns, json loads the task list, and a regex checks
each ``tid: W seconds`` token. ``iter_jobs`` skips a blank line and the header
before parsing, which is how the queue and server functions ignore the
generator's first row. ``raw_line`` is the stripped input, and that string is
what a server function writes downstream so the next hop sees the same tasks.

A packet row adds two columns, ``Dst IP`` and ``TTL``. They give the starting
values of the fields on ``Job``. Rows without them still parse, with those
fields at their defaults.
"""

from __future__ import annotations

import csv
import io
import ipaddress
import json
import math
import re
from dataclasses import dataclass
from typing import Iterable, Iterator

HEADER = "Job Id,arrival time,Tasks"
PACKET_HEADER = f"{HEADER},Dst IP,TTL"

# ``0: W 0.250000`` — tid, then a non-negative seconds token.
_TASK_RE = re.compile(r"^(\d+):\s*W\s*([0-9]+(?:\.[0-9]+)?)$")


class JobParseError(ValueError):
    """A line is not a wait-task job row."""


@dataclass(frozen=True)
class Task:
    """One sleep performed by a server function, in order of ``tid``."""

    tid: int
    wait_seconds: float


@dataclass(frozen=True)
class Job:
    """One generated or forwarded job.

    ``raw_line`` is the CSV row without a trailing newline. Server functions
    forward this string so the next queue sees the same tasks.

    ``dst_ip``, ``ttl``, and ``next_hop`` are the packet header. On a gRPC edge
    they travel as message fields, so they can change while ``raw_line`` does not.
    """

    job_id: str
    arrival_time: float
    tasks: tuple[Task, ...]
    raw_line: str
    dst_ip: int = 0
    ttl: int = 0
    next_hop: str = ""


def ipv4_to_int(text: str) -> int:
    """Dotted IPv4 address to a 32-bit integer. Raises ``ValueError`` when invalid."""
    return int(ipaddress.IPv4Address(text))


def int_to_ipv4(value: int) -> str:
    """32-bit integer to a dotted IPv4 address."""
    return str(ipaddress.IPv4Address(value))


def format_wait(seconds: float) -> str:
    """Wait token used inside a task and inside TASK-START / TASK-END."""
    return f"W {seconds:.6f}"


def format_job_line(
    job_id: str,
    arrival_time: float,
    tasks: Iterable[Task],
    *,
    dst_ip: int | None = None,
    ttl: int | None = None,
) -> str:
    """Serialize one job. The Tasks field uses Lab 0 doubled quotes.

    Passing ``dst_ip`` and ``ttl`` together writes a packet row.
    """
    if (dst_ip is None) != (ttl is None):
        raise ValueError("a packet row needs both dst_ip and ttl")
    task_list = tuple(tasks)
    if not task_list:
        raise ValueError("a job needs at least one wait task")
    encoded_tasks = [f"{task.tid}: {format_wait(task.wait_seconds)}" for task in task_list]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="")
    row = [job_id, f"{arrival_time:.3f}", json.dumps(encoded_tasks)]
    if dst_ip is not None:
        row.extend([int_to_ipv4(dst_ip), str(ttl)])
    writer.writerow(row)
    return buffer.getvalue()


def is_header(line: str) -> bool:
    """True for the workload CSV header row."""
    return line.strip() in (HEADER, PACKET_HEADER)


def parse_job_line(line: str) -> Job:
    """Parse one job row. Header and blank lines are rejected."""
    raw_line = line.strip()
    if raw_line == "":
        raise JobParseError("empty line")
    if is_header(raw_line):
        raise JobParseError("header line")

    try:
        columns = next(csv.reader([raw_line]))
    except csv.Error as exc:
        raise JobParseError(f"malformed csv: {exc}") from exc
    if len(columns) not in (3, 5):
        raise JobParseError(f"expected 3 or 5 columns, found {len(columns)}")

    job_id, arrival_text, tasks_text = columns[:3]
    if job_id == "":
        raise JobParseError("missing job id")
    arrival_time = _parse_seconds(arrival_text, "arrival time")
    tasks = _parse_tasks(tasks_text)
    dst_ip, ttl = _parse_packet_columns(columns[3:])
    return Job(
        job_id=job_id,
        arrival_time=arrival_time,
        tasks=tasks,
        raw_line=raw_line,
        dst_ip=dst_ip,
        ttl=ttl,
    )


def iter_jobs(lines: Iterable[str]) -> Iterator[Job]:
    """Yield jobs, skipping blanks and one header. Other bad lines raise."""
    for line in lines:
        if not line.strip() or is_header(line):
            continue
        yield parse_job_line(line)


def _parse_packet_columns(columns: list[str]) -> tuple[int, int]:
    if not columns:
        return 0, 0
    dst_text, ttl_text = columns
    try:
        dst_ip = ipv4_to_int(dst_text)
    except ValueError as exc:
        raise JobParseError(f"bad destination address {dst_text!r}") from exc
    try:
        ttl = int(ttl_text)
    except ValueError as exc:
        raise JobParseError(f"TTL {ttl_text!r} is not an integer") from exc
    if ttl < 0:
        raise JobParseError("TTL must be >= 0")
    return dst_ip, ttl


def _parse_seconds(text: str, label: str) -> float:
    try:
        value = float(text)
    except ValueError as exc:
        raise JobParseError(f"{label} is not a number") from exc
    if not math.isfinite(value) or value < 0:
        raise JobParseError(f"{label} must be a finite number >= 0")
    return value


def _parse_tasks(text: str) -> tuple[Task, ...]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JobParseError(f"tasks are not a JSON list: {exc}") from exc
    if not isinstance(payload, list) or not payload:
        raise JobParseError("tasks must be a non-empty JSON list")

    tasks: list[Task] = []
    for item in payload:
        if not isinstance(item, str):
            raise JobParseError("each task must be a string")
        match = _TASK_RE.match(item.strip())
        if match is None:
            raise JobParseError(f"unrecognized task {item!r}")
        tid = int(match.group(1))
        wait_seconds = _parse_seconds(match.group(2), "wait")
        tasks.append(Task(tid=tid, wait_seconds=wait_seconds))
    return tuple(tasks)
