"""Regional routing, worker isolation, and multi-queue accounting checks."""

import csv
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from fd.order import validate_order
from lib.job import iter_jobs
from fd.network_config import validate_config
from run_network import run as run_network
from run_hosted import run as run_hosted, workload

try:
    import grpc
except ImportError:
    grpc = None


def config():
    return {
        "restaurant_regions": [0, 0, 1, 1],
        "restaurant_weights": [0.4, 0.1, 0.3, 0.2],
        "kitchen_workers": [1] * 4,
        "couriers": [2, 2],
        "kitchen_capacity": 32,
        "pickup_capacity": 32,
        "count": 60,
        "arrival_mean": 0.02,
        "time_scale": 0.2,
        "seed": 4,
    }


class NetworkTests(unittest.TestCase):
    def test_bad_mapping_rejected(self):
        c = config()
        c["restaurant_regions"] = [0, 0, 2, 2]
        with self.assertRaises(ValueError):
            validate_config(c)

    def test_resource_changes_preserve_absolute_plan(self):
        original = config()
        changed = {**original, "couriers": [3, 4], "kitchen_workers": [2] * 4}
        self.assertEqual(workload(original), workload(changed))
        plan = workload(original)
        self.assertTrue(all(a[0] < b[0] for a, b in zip(plan, plan[1:])))


@unittest.skipUnless(grpc, "requires Python gRPC dependencies")
class RegionalRunTests(unittest.TestCase):
    BACKENDS = [("serial", "food_server", run_network), ("hosted", "food_host", run_hosted)]

    def test_shared_regions_route_and_drain(self):
        for backend, binary, run in self.BACKENDS:
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as tmp:
                if not (APP / "cpp/build" / binary).is_file():
                    self.skipTest(f"requires built {binary}")
                c = config()
                result = run(tmp, c, timeout=30)
                self.assertEqual(result["queues"], 6)
                self.assertEqual(result["generated"], c["count"])
                self.assertEqual(result["delivered"], c["count"])
                self.assertEqual(result["dropped"], 0)
                self.assertEqual(result["incomplete"], 0)
                root = Path(tmp)
                events = (root / "analysis/events.log").read_text()
                for qid in range(6):
                    self.assertIn(f" IN q{qid} ", events)
                for region in (0, 1):
                    for sink in (root / f"region-{region}").glob("sink-*.csv"):
                        for job in iter_jobs(sink.read_text().splitlines()):
                            self.assertEqual(
                                c["restaurant_regions"][validate_order(job).restaurant], region
                            )
                    state = json.loads((root / f"region-{region}/dispatch-state.json").read_text())
                    self.assertEqual(state["waiting"], [])
                    self.assertTrue(
                        all(
                            worker["status"] == "available" for worker in state["couriers"].values()
                        )
                    )
                if backend == "hosted":
                    self.assertEqual(result["server_processes"], 2)
                    runtime = json.loads((root / "runtime.json").read_text())
                    self.assertEqual(runtime["logical_queues"], 6)
                    for pid in runtime["hosts"].values():
                        with self.assertRaises(ProcessLookupError):
                            os.kill(pid, 0)

    def test_overload_is_accounted(self):
        c = {
            **config(),
            "count": 500,
            "arrival_mean": 0,
            "pickup_capacity": 1,
            "kitchen_capacity": 1,
            "couriers": [1, 1],
        }
        for backend, binary, run in self.BACKENDS:
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as tmp:
                if not (APP / "cpp/build" / binary).is_file():
                    self.skipTest(f"requires built {binary}")
                result = run(tmp, c, timeout=30)
                self.assertEqual(result["generated"], c["count"])
                self.assertGreater(result["dropped"], 0)
                self.assertEqual(result["generated"], result["delivered"] + result["dropped"])
                self.assertEqual(result["incomplete"], 0)


@unittest.skipUnless(
    grpc and (APP / "cpp/build/food_host").is_file(), "requires built food_host and gRPC"
)
class HostedScaleTests(unittest.TestCase):
    def test_unvisited_queues_remain_in_metrics(self):
        c = {**config(), "count": 1, "arrival_mean": 0}
        with tempfile.TemporaryDirectory() as tmp:
            result = run_hosted(tmp, c, timeout=30)
            self.assertEqual(result["delivered"], 1)
            with (Path(tmp) / "analysis/qmet.csv").open() as f:
                self.assertEqual({int(r["queue-sys-id"]) for r in csv.DictReader(f)}, set(range(6)))

    def test_deadline_is_failed_run_not_success(self):
        c = {**config(), "count": 10, "arrival_mean": 5, "time_scale": 1}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Exception):
                run_hosted(tmp, c, timeout=0.5)
            root = Path(tmp)
            self.assertEqual(json.loads((root / "status.json").read_text())["status"], "failed")
            self.assertFalse((root / "analysis/summary.json").exists())

    def test_two_hundred_queues(self):
        c = {
            **config(),
            "restaurant_regions": [r for r in range(50) for _ in range(3)],
            "restaurant_weights": [1] * 150,
            "kitchen_workers": [2] * 150,
            "couriers": [4] * 50,
            "count": 3000,
            "arrival_mean": 0.002,
            "time_scale": 0.1,
        }
        with tempfile.TemporaryDirectory() as tmp:
            result = run_hosted(tmp, c, timeout=60)
            self.assertEqual(result["queues"], 200)
            self.assertEqual(result["generated"], result["delivered"] + result["dropped"])
            self.assertEqual(result["server_processes"], 2)
            with (Path(tmp) / "analysis/qmet.csv").open() as f:
                self.assertEqual(
                    {int(r["queue-sys-id"]) for r in csv.DictReader(f)}, set(range(200))
                )


if __name__ == "__main__":
    unittest.main()
