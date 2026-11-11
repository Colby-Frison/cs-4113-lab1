"""Real Python/C++ gRPC integration tests; build cpp/ and install grpcio first."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from run_grpc import run
from lib.job import HEADER, Task, format_job_line

try:
    import grpc
except ImportError:
    grpc = None

BINARY = APP / "cpp/build/food_server"


@unittest.skipUnless(
    grpc is not None and BINARY.is_file(), "requires C++ build and Python gRPC dependencies"
)
class CppGrpcTests(unittest.TestCase):
    def test_live_modes_and_registry(self):
        config = json.loads((APP / "topology/baseline.json").read_text())
        for mode in ("stages", "monolith"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                summary = run(tmp, {**config, "count": 12, "time_scale": 0.2}, mode, timeout=10)
                self.assertEqual(summary["delivered"], 12)
                self.assertEqual(summary["dropped"], 0)
                self.assertEqual(summary["incomplete"], 0)
                events = (Path(tmp) / "analysis/events.log").read_text()
                self.assertEqual(events.count("TASK-START 0"), 12)
                self.assertEqual(events.count("TASK-START 1"), 12)
                if mode == "stages":
                    state = json.loads((Path(tmp) / "dispatch-state.json").read_text())
                    self.assertEqual(state["waiting"], [])
                    self.assertEqual(sum(c["completed"] for c in state["couriers"].values()), 12)
                    self.assertTrue(
                        all(c["status"] == "available" for c in state["couriers"].values())
                    )

    def test_pickup_overflow_is_accounted_as_drop(self):
        config = json.loads((APP / "topology/baseline.json").read_text())

        def fast_kitchen(out, **kwargs):
            out.write(HEADER + "\n")
            for seq in range(20):
                row = format_job_line(
                    f"o{seq}.r0.near.i1", 10, (Task(0, 0), Task(1, 0.1)), wait_precision=3
                )
                out.write(row + "\n")
                out.flush()

        with tempfile.TemporaryDirectory() as tmp, patch("run_grpc.generate", fast_kitchen):
            summary = run(
                tmp,
                {**config, "kitchen_capacity": 30, "pickup_capacity": 1, "couriers": 1},
                timeout=10,
            )
            self.assertGreater(summary["dropped"], 0)
            self.assertEqual(summary["generated"], summary["delivered"] + summary["dropped"])
            self.assertIn("ERROR QUEUE-FULL", (Path(tmp) / "q1.log").read_text())
            self.assertEqual(summary["incomplete"], 0)

    def test_timeout_reaps_servers(self):
        config = json.loads((APP / "topology/baseline.json").read_text())
        processes = []
        real_popen = subprocess.Popen

        def track(*args, **kwargs):
            p = real_popen(*args, **kwargs)
            processes.append(p)
            return p

        with tempfile.TemporaryDirectory() as tmp, patch("run_grpc.subprocess.Popen", track):
            with self.assertRaises(TimeoutError):
                run(tmp, {**config, "time_scale": 100}, "monolith", timeout=0.3)
        self.assertTrue(processes)
        self.assertTrue(all(p.poll() is not None for p in processes))


if __name__ == "__main__":
    unittest.main()
