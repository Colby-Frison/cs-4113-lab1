"""Optional Prometheus metrics for queues and server functions.

Call ``start_metrics`` once when ``--metrics-port`` is set. Every other
helper is a no-op until then, so studies and tests stay unchanged without
the flag. Series are labeled by ``qid`` so a hosted process can export many
queues on one scrape target.
"""

from __future__ import annotations

import threading
import time
from typing import Any

_enabled = False
_lock = threading.Lock()
_admit_times: dict[int, float] = {}
_qsize: dict[str, int] = {}

_queue_depth: Any = None
_queue_depth_ratio: Any = None
_jobs_dropped: Any = None
_jobs_admitted: Any = None
_jobs_departed: Any = None
_queue_wait: Any = None
_jobs_in_service: Any = None
_server_workers: Any = None
_forward_errors: Any = None
_visits: Any = None
_tasks_completed: Any = None
_visit_seconds: Any = None
_task_wait_seconds: Any = None


def start_metrics(port: int) -> None:
    """Bind ``/metrics`` on ``port``. Safe to call once per process."""
    global _enabled
    global _queue_depth, _queue_depth_ratio, _jobs_dropped, _jobs_admitted
    global _jobs_departed, _queue_wait, _jobs_in_service, _server_workers
    global _forward_errors, _visits, _tasks_completed, _visit_seconds
    global _task_wait_seconds

    if _enabled:
        return
    if port < 1 or port > 65535:
        raise SystemExit("--metrics-port must be between 1 and 65535")
    try:
        from prometheus_client import Counter, Gauge, Histogram, start_http_server
    except ImportError as exc:
        raise SystemExit(
            "metrics need prometheus_client. "
            "pip install -r implementation/obj-a/requirements.txt"
        ) from exc

    _queue_depth = Gauge("queue_depth", "Jobs waiting in the buffer", ["qid"])
    _queue_depth_ratio = Gauge(
        "queue_depth_ratio",
        "Buffer occupancy divided by --qsize (0 when unbounded)",
        ["qid"],
    )
    _jobs_dropped = Counter(
        "jobs_dropped_total",
        "Jobs refused by a full buffer",
        ["qid", "reason"],
    )
    _jobs_admitted = Counter("jobs_admitted_total", "Jobs accepted into the buffer", ["qid"])
    _jobs_departed = Counter(
        "jobs_departed_total",
        "Jobs that left the buffer on a successful forward",
        ["qid"],
    )
    _queue_wait = Histogram(
        "queue_wait_seconds",
        "Time from admit to buffer take",
        ["qid"],
        buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    )
    _jobs_in_service = Gauge(
        "jobs_in_service",
        "Visits currently inside serve_job",
        ["qid"],
    )
    _server_workers = Gauge(
        "server_workers",
        "Configured concurrency for utilization (Num. Servers)",
        ["qid"],
    )
    _forward_errors = Counter(
        "forward_errors_total",
        "Visits that could not forward the job",
        ["qid", "reason"],
    )
    _visits = Counter("visits_total", "Completed server visits", ["qid"])
    _tasks_completed = Counter("tasks_completed_total", "TASK-END sleeps finished", ["qid"])
    _visit_seconds = Histogram(
        "visit_seconds",
        "Wall time of one serve_job visit",
        ["qid"],
        buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    )
    _task_wait_seconds = Histogram(
        "task_wait_seconds",
        "Configured wait slept per task",
        ["qid"],
        buckets=(0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0),
    )
    start_http_server(port)
    _enabled = True


def enabled() -> bool:
    return _enabled


def set_queue_capacity(qid: str, qsize: int | None) -> None:
    """Remember ``--qsize`` so depth ratio can be computed."""
    with _lock:
        if qsize is None:
            _qsize.pop(qid, None)
        else:
            _qsize[qid] = qsize


def set_server_workers(qid: str, workers: int) -> None:
    if not _enabled or workers < 1:
        return
    _server_workers.labels(qid=qid).set(workers)


def set_queue_depth(qid: str, depth: int) -> None:
    if not _enabled:
        return
    _queue_depth.labels(qid=qid).set(depth)
    capacity = _qsize.get(qid)
    ratio = (depth / capacity) if capacity else 0.0
    _queue_depth_ratio.labels(qid=qid).set(ratio)


def record_admit(qid: str, job: object) -> None:
    if not _enabled:
        return
    _jobs_admitted.labels(qid=qid).inc()
    with _lock:
        _admit_times[id(job)] = time.monotonic()


def record_drop(qid: str, *, reason: str = "queue_full") -> None:
    if not _enabled:
        return
    _jobs_dropped.labels(qid=qid, reason=reason).inc()


def record_depart(qid: str, job: object) -> None:
    if not _enabled:
        return
    _jobs_departed.labels(qid=qid).inc()
    _observe_wait(qid, job)


def record_buffer_take(qid: str, job: object) -> None:
    """Job left the buffer; record wait even when the forward fails."""
    if not _enabled:
        return
    _observe_wait(qid, job)


def begin_visit(qid: str) -> float:
    if not _enabled:
        return 0.0
    _jobs_in_service.labels(qid=qid).inc()
    return time.monotonic()


def end_visit(qid: str, started: float, *, tasks: int = 0) -> None:
    if not _enabled:
        return
    _jobs_in_service.labels(qid=qid).dec()
    _visits.labels(qid=qid).inc()
    if tasks:
        _tasks_completed.labels(qid=qid).inc(tasks)
    if started:
        _visit_seconds.labels(qid=qid).observe(max(0.0, time.monotonic() - started))


def record_task_wait(qid: str, seconds: float) -> None:
    if not _enabled:
        return
    _task_wait_seconds.labels(qid=qid).observe(max(0.0, seconds))


def record_forward_error(qid: str, *, reason: str) -> None:
    if not _enabled:
        return
    _forward_errors.labels(qid=qid, reason=reason).inc()


def _observe_wait(qid: str, job: object) -> None:
    with _lock:
        started = _admit_times.pop(id(job), None)
    if started is not None:
        _queue_wait.labels(qid=qid).observe(max(0.0, time.monotonic() - started))
