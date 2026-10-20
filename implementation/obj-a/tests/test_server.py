"""Server-function visit: sleep each wait, or log NO-OUT-EDGE."""

from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path

from support import ROOT, capture_logger, event_kinds, load_script

from lib.job import Task, format_job_line, iter_jobs, parse_job_line
from lib.server import serve_job

STATELESS = load_script(
    ROOT / "server-func-stateless" / "server_func.py",
    "server_func_stateless",
)


class ServeJobTests(unittest.TestCase):
    def test_task_loop_sleeps_then_forwards(self) -> None:
        job = parse_job_line(format_job_line("j0", 1.5, (Task(0, 0.5), Task(1, 0.25))))
        slept: list[float] = []
        log, messages = capture_logger("serve-forward")
        with tempfile.TemporaryDirectory() as tmp:
            dest = str(Path(tmp) / "sink.csv")
            chosen = serve_job(
                job,
                [dest],
                "q0",
                0,
                log,
                sleep_fn=slept.append,
                rng=random.Random(0),
            )
            self.assertEqual(chosen, dest)
            forwarded = list(iter_jobs(Path(dest).read_text(encoding="utf-8").splitlines()))
        self.assertEqual(slept, [0.5, 0.25])
        self.assertEqual(len(forwarded), 1)
        self.assertEqual(forwarded[0].raw_line, job.raw_line)
        self.assertEqual(
            event_kinds(messages),
            [
                "ENTERS-SERVER",
                "TASK-START",
                "TASK-END",
                "TASK-START",
                "TASK-END",
                "EXITS-SERVER",
                "DEPART-VIA",
            ],
        )

    def test_missing_outfile_logs_no_out_edge(self) -> None:
        job = parse_job_line(format_job_line("j0", 1.0, (Task(0, 0.1),)))
        log, messages = capture_logger("serve-no-edge")
        chosen = serve_job(job, [], "q0", 0, log, sleep_fn=lambda _seconds: None)
        self.assertIsNone(chosen)
        self.assertEqual(
            event_kinds(messages),
            ["ENTERS-SERVER", "TASK-START", "TASK-END", "EXITS-SERVER", "ERROR NO-OUT-EDGE"],
        )

    def test_stateful_visit_count_is_per_job_id(self) -> None:
        job = parse_job_line(format_job_line("j0", 1.0, (Task(0, 0.0),)))
        log, _messages = capture_logger("serve-state")
        state: dict[str, int] = {}
        serve_job(job, [], "q0", 0, log, state=state, sleep_fn=lambda _seconds: None)
        serve_job(job, [], "q0", 0, log, state=state, sleep_fn=lambda _seconds: None)
        self.assertEqual(state, {"j0": 2})

    def test_grpc_flag_is_rejected(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            STATELESS.main(["--ingrpc", "0.0.0.0,1", "-i", "in.csv"])
        self.assertIn("not implemented", str(raised.exception).lower())


if __name__ == "__main__":
    unittest.main()
