"""File-based run of the generator, queue, and stateless server function."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import ROOT, event_kinds, log_bodies

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

            q_messages = log_bodies(queue_log.read_text(encoding="utf-8"))
            sf_messages = log_bodies(server_log.read_text(encoding="utf-8"))
            q_kinds = event_kinds(q_messages)
            sf_kinds = event_kinds(sf_messages)
            self.assertEqual(q_kinds.count("ARRIVE-VIA"), 2)
            self.assertEqual(q_kinds.count("DEPART-VIA"), 2)
            self.assertNotIn("ERROR QUEUE-FULL", q_kinds)
            self.assertGreaterEqual(sum(message.startswith("metric-q-size ") for message in q_messages), 1)
            self.assertEqual(sf_kinds.count("ENTERS-SERVER"), 2)
            self.assertEqual(sf_kinds.count("TASK-START"), 2)
            self.assertEqual(sf_kinds.count("TASK-END"), 2)
            self.assertEqual(sf_kinds.count("EXITS-SERVER"), 2)
            self.assertEqual(sf_kinds.count("DEPART-VIA"), 2)
            self.assertTrue(all("IN q0 " in message for message in q_messages if " IN " in message))
            self.assertTrue(all("ENTERS-SERVER 0" in message for message in sf_messages if "ENTERS-SERVER" in message))

            generated = list(iter_jobs(jobs.read_text(encoding="utf-8").splitlines()))
            delivered = list(iter_jobs(sink.read_text(encoding="utf-8").splitlines()))
            self.assertEqual([job.job_id for job in generated], ["j0", "j1"])
            self.assertEqual([job.job_id for job in delivered], [job.job_id for job in generated])
            self.assertEqual([job.raw_line for job in delivered], [job.raw_line for job in generated])


if __name__ == "__main__":
    unittest.main()
