"""A few tickets through the hosted pair, not a size sweep."""

from __future__ import annotations

import unittest
from pathlib import Path
import tempfile

import support

from mm.ticket import BANDS
from studies.expand import build_layout
from studies.run_study import csv_rows, execute_hosted

del support


class HostedSmokeTests(unittest.TestCase):
    def test_retries_land_on_the_same_shard(self) -> None:
        pattern = {
            "size": "smoke",
            "replicas": 2,
            "baseline_search_workers": 1,
            "shared_workers": 1,
            "qsize": 8,
            "count": 4,
            "time_scale": 0.0,
            "ready_fail_p": 1.0,
            "max_ready_visits": 2,
            "target_rho": 0.8,
            "seed": 7,
        }
        layout = build_layout(pattern, regions=("na",), modes=("ranked",), bands=BANDS)
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "smoke"
            execute_hosted("smoke", layout, 0, run_dir, 4, 0.0, 0.0, region="na", mode="ranked")
            self.assertEqual(csv_rows(run_dir / "failed.csv"), 4)
            self.assertEqual(csv_rows(run_dir / "matched.csv"), 0)
            log = (run_dir / "sf-host.log").read_text(encoding="utf-8")
            self.assertIn("gRPC listen", log)
            self.assertIn("ENTERS-SERVER", log)
            self.assertRegex((run_dir / "q-host.log").read_text(encoding="utf-8"), r"IN q\d+ ")
            search_ids = {queue.qid for queue in layout.search_queues}
            visits: dict[str, list[str]] = {}
            for line in log.splitlines():
                message = line.split(" - ")[-1]
                parts = message.split()
                if len(parts) < 5 or parts[4] != "ENTERS-SERVER":
                    continue
                job, qid = parts[1], parts[3]
                if qid not in search_ids:
                    continue
                visits.setdefault(job, []).append(qid)
            self.assertTrue(visits)
            for qids in visits.values():
                self.assertEqual(qids, [qids[0], qids[0]])


if __name__ == "__main__":
    unittest.main()
