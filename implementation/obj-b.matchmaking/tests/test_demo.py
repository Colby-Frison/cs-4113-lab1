"""File-edge monolith and staged pipeline, with zero sleeps."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support

from lib.job import iter_jobs
from mm.demo import main
from mm.ticket import parse_ticket

del support


class DemoTests(unittest.TestCase):
    def test_both_topologies_account_for_every_ticket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / "demo"
            code = main(
                [
                    "--which",
                    "both",
                    "--workdir",
                    str(workdir),
                    "--count",
                    "4",
                    "--time-scale",
                    "0",
                    "--arrival-mean",
                    "0",
                    "--seed",
                    "1",
                ]
            )
            self.assertEqual(code, 0)
            mono_jobs = _jobs(workdir / "monolith" / "jobs.csv")
            mono_matched = _jobs(workdir / "monolith" / "matched.csv")
            self.assertEqual([job.job_id for job in mono_matched], [job.job_id for job in mono_jobs])
            log = (workdir / "monolith" / "server.log").read_text(encoding="utf-8")
            self.assertEqual(log.count("TASK-START"), 24)

            stage_jobs = _jobs(workdir / "stages" / "jobs.csv")
            matched = _jobs(workdir / "stages" / "matched.csv")
            failed = _jobs(workdir / "stages" / "failed.csv")
            accounted = [job.job_id for job in matched] + [job.job_id for job in failed]
            self.assertCountEqual(accounted, [job.job_id for job in stage_jobs])
            for job in matched + failed:
                self.assertIsNotNone(parse_ticket(job.job_id))


def _jobs(path: Path):
    return list(iter_jobs(path.read_text(encoding="utf-8").splitlines()))


if __name__ == "__main__":
    unittest.main()
