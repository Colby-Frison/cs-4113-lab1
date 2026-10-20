"""Workload generator CLI."""

from __future__ import annotations

import random
import tempfile
import time
import unittest
from pathlib import Path

from support import ROOT, load_script

from lib.job import HEADER, iter_jobs

GEN = load_script(ROOT / "workload-generator" / "workload_generator.py", "workload_generator")


class GeneratorTests(unittest.TestCase):
    def test_count_writes_header_and_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "jobs.csv"
            code = GEN.main(
                [
                    "--arrival-dist",
                    "fixed",
                    "--arrival-fixed",
                    "0",
                    "--wait-dist",
                    "fixed",
                    "--wait-fixed",
                    "0.25",
                    "--tasks",
                    "2",
                    "--count",
                    "3",
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(code, 0)
            lines = out.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], HEADER)
            jobs = list(iter_jobs(lines))
            self.assertEqual([job.job_id for job in jobs], ["j0", "j1", "j2"])
            for job in jobs:
                self.assertEqual(len(job.tasks), 2)
                self.assertEqual([task.wait_seconds for task in job.tasks], [0.25, 0.25])

    def test_duration_stops_during_the_arrival_sleep(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "jobs.csv"
            started = time.monotonic()
            code = GEN.main(
                [
                    "--arrival-dist",
                    "fixed",
                    "--arrival-fixed",
                    "30",
                    "--wait-dist",
                    "fixed",
                    "--wait-fixed",
                    "0",
                    "--duration",
                    "0.05",
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(code, 0)
            self.assertLess(time.monotonic() - started, 2.0)
            self.assertEqual(out.read_text(encoding="utf-8").splitlines(), [HEADER])

    def test_count_wins_when_both_limits_are_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "jobs.csv"
            started = time.monotonic()
            code = GEN.main(
                [
                    "--arrival-dist",
                    "fixed",
                    "--arrival-fixed",
                    "0",
                    "--wait-dist",
                    "fixed",
                    "--wait-fixed",
                    "0.1",
                    "--count",
                    "1",
                    "--duration",
                    "30",
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(code, 0)
            self.assertLess(time.monotonic() - started, 2.0)
            jobs = list(iter_jobs(out.read_text(encoding="utf-8").splitlines()))
            self.assertEqual([job.job_id for job in jobs], ["j0"])
            self.assertEqual([task.wait_seconds for task in jobs[0].tasks], [0.1])

    def test_missing_uniform_bounds_fail(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            GEN.main(
                [
                    "--arrival-dist",
                    "uniform",
                    "--wait-dist",
                    "fixed",
                    "--wait-fixed",
                    "1",
                    "--count",
                    "1",
                ]
            )
        self.assertIn("arrival-low", str(raised.exception))

    def test_normal_draw_is_clamped_at_zero(self) -> None:
        class NegativeGauss(random.Random):
            def gauss(self, mu: float, sigma: float) -> float:
                return -3.0

        draw = GEN.make_draw("normal", {"mu": 1.0, "sigma": 0.5}, NegativeGauss())
        self.assertEqual(draw(), 0.0)


if __name__ == "__main__":
    unittest.main()
