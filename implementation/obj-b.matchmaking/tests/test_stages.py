"""Routing rules and a file-at-a-time ready-check cap."""

from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path

import support

from lib.job import iter_jobs
from mm.runtime import run_monolith, run_stage
from mm.stages import choose_ready, choose_route
from mm.workload import WorkloadConfig, generate

del support


class StageRuleTests(unittest.TestCase):
    def test_route_uses_the_region_table(self) -> None:
        routes = {"na": "search-na.csv", "eu": "search-eu.csv"}
        self.assertEqual(choose_route("na", routes), "search-na.csv")
        self.assertIsNone(choose_route("ap", routes))

    def test_ready_failure_retries_until_the_cap(self) -> None:
        self.assertEqual(
            choose_ready(1, random.Random(0), 1.0, 2, "pass", "retry", "fail"),
            "retry",
        )
        self.assertEqual(
            choose_ready(2, random.Random(0), 1.0, 2, "pass", "retry", "fail"),
            "fail",
        )
        self.assertEqual(
            choose_ready(2, random.Random(0), 0.0, 2, "pass", "retry", "fail"),
            "pass",
        )


class ReadyFileTests(unittest.TestCase):
    def test_second_failure_goes_to_the_failed_sink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = root / "jobs.csv"
            with jobs.open("w", encoding="utf-8", newline="") as handle:
                generate(
                    WorkloadConfig(
                        count=1,
                        seed=1,
                        time_scale=0.0,
                        arrival_mean=0.0,
                        region="na",
                        mode="ranked",
                    ),
                    handle,
                    sleep_fn=lambda _seconds: None,
                    now_fn=lambda: 10.0,
                )
            state = root / "ready-state.txt"
            retry = root / "retry.csv"
            failed = root / "failed.csv"
            passed = root / "passed.csv"
            code = run_stage(
                [
                    "--stage",
                    "ready",
                    "-i",
                    str(jobs),
                    "--qid",
                    "q-ready",
                    "--pass",
                    str(passed),
                    "--retry",
                    str(retry),
                    "--fail",
                    str(failed),
                    "--ready-fail-p",
                    "1",
                    "--max-ready-visits",
                    "2",
                    "--state-file",
                    str(state),
                    "--seed",
                    "1",
                    "--logfile",
                    str(root / "ready-1.log"),
                ]
            )
            self.assertEqual(code, 0)
            ticket_id = _ids(jobs)[0]
            self.assertEqual(_ids(retry), [ticket_id])
            self.assertEqual(_ids(failed), [])
            code = run_stage(
                [
                    "--stage",
                    "ready",
                    "-i",
                    str(retry),
                    "--qid",
                    "q-ready",
                    "--pass",
                    str(passed),
                    "--retry",
                    str(root / "retry-2.csv"),
                    "--fail",
                    str(failed),
                    "--ready-fail-p",
                    "1",
                    "--max-ready-visits",
                    "2",
                    "--state-file",
                    str(state),
                    "--seed",
                    "1",
                    "--logfile",
                    str(root / "ready-2.log"),
                ]
            )
            self.assertEqual(code, 0)
            self.assertEqual(_ids(failed), [ticket_id])
            self.assertEqual(state.read_text(encoding="utf-8").strip(), f"{ticket_id} 2")

    def test_monolith_skips_a_row_that_is_not_a_ticket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "in.csv"
            sink = root / "out.csv"
            with source.open("w", encoding="utf-8", newline="") as handle:
                generate(
                    WorkloadConfig(
                        count=1,
                        seed=2,
                        time_scale=0.0,
                        arrival_mean=0.0,
                        region="na",
                        mode="ranked",
                    ),
                    handle,
                    sleep_fn=lambda _seconds: None,
                    now_fn=lambda: 10.0,
                )
                handle.write('j0,1.000,"[""0: W 0.000000""]"\n')
            code = run_monolith(
                ["-i", str(source), "-o", str(sink), "--qid", "q-match", "--logfile", str(root / "s.log")]
            )
            self.assertEqual(code, 0)
            ids = _ids(sink)
            self.assertEqual(len(ids), 1)
            self.assertTrue(ids[0].startswith("t0."))
            log = (root / "s.log").read_text(encoding="utf-8")
            self.assertIn("skipping j0", log)
            self.assertEqual(log.count("TASK-START"), 6)


def _ids(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [job.job_id for job in iter_jobs(path.read_text(encoding="utf-8").splitlines())]


if __name__ == "__main__":
    unittest.main()
