"""File-based run of the generator, queue, and stateless server function."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import ROOT

from lib.job import iter_jobs

GEN = ROOT / "workload-generator" / "workload_generator.py"
QUEUE = ROOT / "queue" / "queue.py"
SERVER = ROOT / "server-func-stateless" / "server_func.py"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{args}\nexit {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


class SmokeTests(unittest.TestCase):
    def test_generate_queue_and_stateless_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = root / "jobs.csv"
            queued = root / "queued.csv"
            sink = root / "sink.csv"
            queue_log = root / "q.log"
            server_log = root / "sf.log"

            _run(
                [
                    str(GEN),
                    "--arrival-dist",
                    "fixed",
                    "--arrival-fixed",
                    "0",
                    "--wait-dist",
                    "fixed",
                    "--wait-fixed",
                    "0.01",
                    "--count",
                    "2",
                    "--out",
                    str(jobs),
                ]
            )
            _run(
                [
                    str(QUEUE),
                    "--qsize",
                    "10",
                    "--qid",
                    "q0",
                    "-i",
                    str(jobs),
                    "-o",
                    str(queued),
                    "--logfile",
                    str(queue_log),
                ]
            )
            _run(
                [
                    str(SERVER),
                    "--qid",
                    "q0",
                    "--server-id",
                    "0",
                    "-i",
                    str(queued),
                    "-o",
                    str(sink),
                    "--logfile",
                    str(server_log),
                ]
            )

            q_text = queue_log.read_text(encoding="utf-8")
            sf_text = server_log.read_text(encoding="utf-8")
            for kind in ("ARRIVE-VIA", "DEPART-VIA", "metric-q-size"):
                self.assertIn(kind, q_text)
            for kind in ("ENTERS-SERVER", "TASK-START", "TASK-END", "EXITS-SERVER", "DEPART-VIA"):
                self.assertIn(kind, sf_text)

            generated = list(iter_jobs(jobs.read_text(encoding="utf-8").splitlines()))
            delivered = list(iter_jobs(sink.read_text(encoding="utf-8").splitlines()))
            self.assertEqual([job.job_id for job in delivered], [job.job_id for job in generated])
            self.assertEqual([job.raw_line for job in delivered], [job.raw_line for job in generated])


if __name__ == "__main__":
    unittest.main()
