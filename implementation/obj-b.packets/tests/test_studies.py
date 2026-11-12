"""Each model's study plan, and the metrics behind the main plots."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import APP, load_script

STUDIES = APP.parents[1] / "analysis" / "obj-b-packets"
STUDY_DIR = STUDIES / "simple"
STUDY = load_script(STUDIES / "run_study.py", "pk_run_study")


def load_study(model: str) -> dict:
    return json.loads((STUDIES / model / "study.json").read_text(encoding="utf-8"))


class TimeWeightedTests(unittest.TestCase):
    def test_value_holds_until_the_next_row(self) -> None:
        # Two packets in service for the first half of a 1000 ms bin, then none.
        result = STUDY.time_weighted([(0, 0, 2), (500, 0, 0)], 1000, 1000)
        self.assertEqual(result["serving"], [1.0])

    def test_a_long_interval_spans_several_bins(self) -> None:
        result = STUDY.time_weighted([(0, 3, 0), (1500, 0, 0)], 2000, 1000)
        self.assertEqual(result["waiting"], [3.0, 1.5])

    def test_many_quick_rows_do_not_outweigh_a_long_state(self) -> None:
        # A plain average of these rows would give about 0.5 rather than 0.9.
        rows = [(0, 0, 1)] + [(900 + i * 20, 0, i % 2) for i in range(5)]
        rows.append((1000, 0, 0))
        result = STUDY.time_weighted(rows, 1000, 1000)
        self.assertAlmostEqual(result["serving"][0], 0.94, places=2)


class EventTests(unittest.TestCase):
    def write(self, directory: str, lines: list[str]) -> Path:
        path = Path(directory) / "events.log"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_only_sink_departures_are_delivered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(
                directory,
                [
                    "10 p0-0 IN q0 ARRIVE-VIA gen-q0.fifo",
                    "60 p0-0 IN q0 DEPART-VIA q1",
                    "70 p0-0 IN q1 ARRIVE-VIA q0",
                    "130 p0-0 IN q1 DEPART-VIA sink-q1.csv",
                    "20 p0-1 IN q0 ARRIVE-VIA gen-q0.fifo",
                    "80 p0-1 IN q0 DEPART-VIA q0",
                    "90 p0-1 IN q1 ERROR QUEUE-FULL",
                ],
            )
            self.assertEqual(STUDY.delivered_latencies(path), {"p0-0": (10, 130)})

    def test_longest_jobs_are_ranked_by_duration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(
                directory,
                [
                    "0 fast IN q0 ARRIVE-VIA g",
                    "10 fast IN q0 DEPART-VIA sink-q0.csv",
                    "0 slow IN q0 ARRIVE-VIA g",
                    "90 slow IN q0 DEPART-VIA sink-q0.csv",
                ],
            )
            self.assertEqual(STUDY.longest_jobs(path, 1), ["slow"])


class PlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.study = load_study("simple")
        cls.plan = STUDY.plan_study(cls.study)

    def test_seven_sets_with_the_requested_budgets(self) -> None:
        totals = {name: entry["total_servers"] for name, entry in self.plan["sets"].items()}
        self.assertEqual(
            totals,
            {"base": 20, "srv-10": 18, "srv-20": 16, "srv-30": 14, "srv+25": 25, "srv+50": 30, "srv+100": 40},
        )

    def test_scale_is_inside_the_task_limits(self) -> None:
        self.assertTrue(2 <= self.study["routers"] <= 10)
        self.assertTrue(1000 <= self.study["packets"] <= 10000)

    def test_busiest_baseline_router_hits_the_target(self) -> None:
        self.assertAlmostEqual(max(self.plan["sets"]["base"]["predicted_utilization"]), 0.7)

    def test_every_reduced_budget_changes_the_allocation(self) -> None:
        base = self.plan["sets"]["base"]["servers"]
        for name in ("srv-10", "srv-20", "srv-30"):
            self.assertNotEqual(self.plan["sets"][name]["servers"], base)

    def test_highlighted_queues_are_the_busiest_at_baseline(self) -> None:
        load = self.plan["sets"]["base"]["predicted_utilization"]
        self.assertEqual(self.plan["highlight"][:2], ["q6", "q9"])
        self.assertEqual(load[6], max(load))


class ScaleTests(unittest.TestCase):
    """The lab's bands: queues and jobs per run at each scale."""

    BANDS = {
        "medium": ((100, 999), (10_000, 100_000)),
        "large": ((1000, 9999), (100_000, 1_000_000)),
        "very-large": ((10_000, 99_999), (100_000, 1_000_000)),
    }

    def test_each_model_is_inside_its_band_with_one_seed(self) -> None:
        for model, ((low, high), (fewest, most)) in self.BANDS.items():
            with self.subTest(model=model):
                study = load_study(model)
                per_router = 3 if study.get("mode") == "stages" else 1
                self.assertTrue(low <= study["routers"] * per_router <= high)
                self.assertTrue(fewest <= study["packets"] <= most)
                self.assertEqual(study["traffic_seeds"], [1])

    def test_the_smallest_budget_still_gives_every_router_a_server(self) -> None:
        for model in ("simple", *self.BANDS):
            with self.subTest(model=model):
                study = load_study(model)
                smallest = round(study["baseline_servers"] * min(study["sets"].values()))
                self.assertGreaterEqual(smallest, study["routers"])

    def test_each_model_has_a_slurm_script(self) -> None:
        for model in ("simple", *self.BANDS):
            script = (STUDIES / model / "run.sbatch").read_text(encoding="utf-8")
            self.assertIn(f"job.sh\" {model} ", script)


class StagePlanTests(unittest.TestCase):
    """A decomposed router has three queues that share its servers."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.study = dict(load_study("simple"), mode="stages")
        cls.plan = STUDY.plan_study(cls.study)

    def test_three_queues_per_router(self) -> None:
        self.assertEqual(len(self.plan["queues"]), 3 * self.study["routers"])
        self.assertEqual(self.plan["queues"][:4], ["q0", "q1", "q2", "q3"])
        self.assertEqual(self.plan["queue_router"][:4], [0, 0, 0, 1])

    def test_the_forward_stage_is_the_bottleneck(self) -> None:
        # Forwarding has the longest service time, so it fills first.
        busiest = self.plan["highlight"][0]
        self.assertEqual(self.plan["queues"].index(busiest) % 3, 2)
        self.assertAlmostEqual(max(self.plan["sets"]["base"]["predicted_utilization"]), 0.7)

    def test_stage_queues_share_their_routers_servers(self) -> None:
        servers = STUDY.queue_servers(self.plan, "base")
        per_router = self.plan["sets"]["base"]["servers"]
        self.assertEqual([servers["q3"], servers["q4"], servers["q5"]], [per_router[1]] * 3)


if __name__ == "__main__":
    unittest.main()
