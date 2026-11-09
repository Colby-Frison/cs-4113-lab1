"""Optional Prometheus /metrics endpoint for queue and server visits."""

from __future__ import annotations

import socket
import time
import unittest
import urllib.error
import urllib.request

from support import ROOT, capture_logger, load_script

from lib import metrics
from lib.job import Job, Task, format_job_line, parse_job_line
from lib.server import serve_job

QUEUE = load_script(ROOT / "queue" / "queue.py", "obj_a_queue_metrics")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _job(job_id: str) -> Job:
    return parse_job_line(format_job_line(job_id, 1.0, (Task(0, 0.01),)))


def _scrape(port: int) -> str:
    deadline = time.monotonic() + 2.0
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=0.5) as response:
                return response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
            time.sleep(0.05)
    raise AssertionError(f"metrics endpoint did not answer: {last_error}")


class MetricsOffTests(unittest.TestCase):
    def test_admit_without_metrics_port_stays_quiet(self) -> None:
        self.assertFalse(metrics.enabled())
        log, _messages = capture_logger("metrics-off")
        buffer = QUEUE.Queue(maxsize=1)
        self.assertTrue(QUEUE.admit_job(buffer, _job("j0"), "q0", "in.csv", log, now=1))


class MetricsOnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if metrics.enabled():
            raise unittest.SkipTest("metrics already started in this process")
        try:
            import prometheus_client  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("prometheus_client is not installed")
        cls.port = _free_port()
        metrics.start_metrics(cls.port)
        metrics.set_queue_capacity("q0", 2)
        metrics.set_server_workers("q0", 1)

    def test_admit_drop_and_serve_export_series(self) -> None:
        log, _messages = capture_logger("metrics-on")
        buffer = QUEUE.Queue(maxsize=1)
        first = _job("m0")
        second = _job("m1")
        self.assertTrue(QUEUE.admit_job(buffer, first, "q0", "in.csv", log, now=1))
        self.assertFalse(QUEUE.admit_job(buffer, second, "q0", "in.csv", log, now=2))
        metrics.set_queue_depth("q0", buffer.qsize())
        taken = buffer.get()
        metrics.record_depart("q0", taken)
        serve_job(taken, [], "q0", 0, log, sleep_fn=lambda _seconds: None)

        body = _scrape(self.port)
        self.assertIn("jobs_admitted_total", body)
        self.assertIn('qid="q0"', body)
        self.assertIn("jobs_dropped_total", body)
        self.assertIn("queue_depth", body)
        self.assertIn("visits_total", body)
        self.assertIn("forward_errors_total", body)


if __name__ == "__main__":
    unittest.main()
