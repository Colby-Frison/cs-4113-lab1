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
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
from dataclasses import dataclass
from typing import Iterable, Iterator

HEADER = "Job Id,arrival time,Tasks"

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
    """

    job_id: str
    arrival_time: float
    tasks: tuple[Task, ...]
    raw_line: str


def format_wait(seconds: float, *, precision: int = 6) -> str:
    """Wait token used inside a task and inside TASK-START / TASK-END."""
    return f"W {seconds:.{precision}f}"


def format_job_line(job_id: str, arrival_time: float, tasks: Iterable[Task], *, wait_precision: int = 6) -> str:
    """Serialize one job. The Tasks field uses Lab 0 doubled quotes."""
    task_list = tuple(tasks)
    if not task_list:
        raise ValueError("a job needs at least one wait task")
    encoded_tasks = [f"{task.tid}: {format_wait(task.wait_seconds, precision=wait_precision)}" for task in task_list]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="")
    writer.writerow([job_id, f"{arrival_time:.3f}", json.dumps(encoded_tasks)])
    return buffer.getvalue()


def is_header(line: str) -> bool:
    """True for the workload CSV header row."""
    return line.strip() == HEADER


def parse_job_line(line: str) -> Job:
    """Parse one job row. Header and blank lines are rejected."""
    raw_line = line.strip()
    if raw_line == "":
        raise JobParseError("empty line")
    if raw_line == HEADER:
        raise JobParseError("header line")

    try:
        columns = next(csv.reader([raw_line]))
    except csv.Error as exc:
        raise JobParseError(f"malformed csv: {exc}") from exc
    if len(columns) != 3:
        raise JobParseError(f"expected 3 columns, found {len(columns)}")

    job_id, arrival_text, tasks_text = columns
    if job_id == "":
        raise JobParseError("missing job id")
    arrival_time = _parse_seconds(arrival_text, "arrival time")
    tasks = _parse_tasks(tasks_text)
    return Job(
        job_id=job_id,
        arrival_time=arrival_time,
        tasks=tasks,
        raw_line=raw_line,
    )


def iter_jobs(lines: Iterable[str]) -> Iterator[Job]:
    """Yield jobs, skipping blanks and one header. Other bad lines raise."""
    for line in lines:
        if not line.strip() or is_header(line):
            continue
        yield parse_job_line(line)


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
