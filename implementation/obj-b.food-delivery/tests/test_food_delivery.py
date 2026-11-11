"""Core workload, dispatch, Python pipeline, and analysis checks."""

import io
import json
import logging
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from fd.order import Order, validate_order
from fd.workload import generate
from fd.server import serve
from fd.analysis import analyze
from fd.dispatch import Dispatcher
from lib.job import Task, format_job_line, parse_job_line, iter_jobs
from run_demo import run


def job():
    return parse_job_line(
        format_job_line(Order(0, 0, "near", 2).job_id, 10, (Task(0, 0.2), Task(1, 0.5)))
    )


def config():
    return {
        **json.loads((APP / "topology/baseline.json").read_text()),
        "count": 8,
        "time_scale": 0.1,
        "arrival_mean": 0.01,
    }


class FoodDeliveryTests(unittest.TestCase):
    def test_dispatch_registry_capacity_and_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            dispatch = Dispatcher(1, 1, tmp)
            first = job()
            second = parse_job_line(format_job_line(Order(1, 0, "near", 2).job_id, 10, first.tasks))
            third = parse_job_line(format_job_line(Order(2, 0, "near", 2).job_id, 10, first.tasks))
            try:
                self.assertTrue(dispatch.admit(first, "prepared"))
                self.assertEqual(dispatch.assign_next(0), first)
                self.assertTrue(dispatch.admit(second, "prepared"))
                self.assertFalse(dispatch.admit(third, "prepared"))
                with self.assertRaises(ValueError):
                    dispatch.assign_next(0)
                with self.assertRaises(ValueError):
                    dispatch.release(0, "wrong-order")
                state = json.loads((Path(tmp) / "dispatch-state.json").read_text())
                self.assertEqual(state["waiting"], [second.job_id])
                self.assertEqual(state["couriers"]["0"]["status"], "busy")
                self.assertEqual(state["couriers"]["0"]["order_id"], first.job_id)

                # step through releases directly, leaving live delivery to the pipeline test
                dispatch.release(0, first.job_id)
                self.assertEqual(dispatch.assign_next(0), second)
                dispatch.release(0, second.job_id)
                self.assertEqual(dispatch.couriers[0]["status"], "available")
                self.assertIsNone(dispatch.couriers[0]["order_id"])
                self.assertEqual(dispatch.couriers[0]["completed"], 2)
                self.assertFalse(dispatch.pending)
            finally:
                for handler in dispatch.log.handlers:
                    handler.close()

    def test_dispatch_reports_delivery_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input.csv"
            source.write_text(job().raw_line + "\n")

            def fail(_):
                raise RuntimeError("delivery unavailable")

            dispatch = Dispatcher(1, 2, root, sleep=fail)
            with self.assertRaisesRegex(RuntimeError, "delivery unavailable"):
                dispatch.run([source])
            self.assertEqual(dispatch.couriers[0]["status"], "failed")

    def test_generator_seed_scaling_and_arrival_pacing(self):
        first, second = io.StringIO(), io.StringIO()
        waits = []
        generate(first, count=4, seed=4, sleep=waits.append, clock=lambda: 10)
        generate(second, count=4, seed=4, sleep=lambda _: None, clock=lambda: 10)
        self.assertEqual(first.getvalue(), second.getvalue())
        self.assertEqual(len(waits), 4)
        for generated in iter_jobs(first.getvalue().splitlines()):
            order = validate_order(generated)
            multiplier = {"near": 1, "mid": 2, "far": 3}[order.zone]
            self.assertGreaterEqual(generated.tasks[1].wait_seconds, 0.04 * multiplier)
            self.assertLessEqual(generated.tasks[1].wait_seconds, 0.06 * multiplier)
        self.assertTrue(all(w > 0 for w in waits))
        scaled = io.StringIO()
        generate(scaled, count=4, seed=4, time_scale=0.5, sleep=lambda _: None, clock=lambda: 10)
        for a, b in zip(
            iter_jobs(first.getvalue().splitlines()), iter_jobs(scaled.getvalue().splitlines())
        ):
            self.assertEqual(a.job_id, b.job_id)
            for ta, tb in zip(a.tasks, b.tasks):
                # each generated duration is independently rounded to milliseconds
                self.assertAlmostEqual(ta.wait_seconds / 2, tb.wait_seconds, delta=0.000751)

    def test_stage_only_performs_its_task_and_preserves_order(self):
        for stage, expected in [("kitchen", [0.2]), ("courier", [0.5]), ("monolith", [0.2, 0.5])]:
            with self.subTest(stage=stage):
                sleeps, out, messages = [], io.StringIO(), io.StringIO()
                log = logging.Logger(stage)
                log.addHandler(logging.StreamHandler(messages))
                serve(job(), stage, "q0", 2, out, "sink.csv", log, sleep=sleeps.append)
                self.assertEqual(sleeps, expected)
                self.assertEqual(out.getvalue().strip(), job().raw_line)
                events = messages.getvalue()
                self.assertLess(events.index("ENTERS-SERVER"), events.index("EXITS-SERVER"))
                if stage == "courier":
                    self.assertLess(events.index("courier-assigned"), events.index("TASK-START"))
                    self.assertLess(events.index("TASK-END"), events.index("courier-released"))

    def test_refuses_to_overwrite_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "keep.txt"
            marker.write_text("keep")
            with self.assertRaises(ValueError):
                run(Path(tmp), config())
            self.assertEqual(marker.read_text(), "keep")

    def test_live_pipeline_and_monolith(self):
        for mode in ("stages", "monolith"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(APP / "run_demo.py"),
                        "--count",
                        "8",
                        "--time-scale",
                        "0.1",
                        "--mode",
                        mode,
                        "--workdir",
                        tmp,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                summary = json.loads((Path(tmp) / "analysis/summary.json").read_text())
                self.assertEqual(summary["generated"], 8)
                self.assertEqual(summary["delivered"], 8)
                self.assertEqual(summary["dropped"], 0)
                events = (Path(tmp) / "analysis/events.log").read_text()
                self.assertEqual(events.count("DEPART-VIA sink"), 8)
                self.assertEqual(events.count("TASK-START 0"), 8)
                self.assertEqual(events.count("TASK-START 1"), 8)
                if mode == "stages":
                    state = json.loads((Path(tmp) / "dispatch-state.json").read_text())
                    self.assertEqual(state["waiting"], [])
                    self.assertEqual(sum(c["completed"] for c in state["couriers"].values()), 8)
                    self.assertTrue(
                        all(c["status"] == "available" for c in state["couriers"].values())
                    )

    def test_downstream_drop_is_not_mistaken_for_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "jobs.csv").write_text(job().raw_line + "\n")
            lines = [
                ("Q", "10.000 o0.r0.near.i2 IN q0 ARRIVE-VIA source"),
                ("sv", "10.010 o0.r0.near.i2 IN q0 ENTERS-SERVER 0"),
                ("sv", "10.210 o0.r0.near.i2 IN q0 EXITS-SERVER 0"),
                ("sv", "10.211 o0.r0.near.i2 IN q0 DEPART-VIA prepared-0"),
                ("Q", "10.212 o0.r0.near.i2 IN q1 ERROR QUEUE-FULL"),
            ]
            (root / "events.log").write_text(
                "".join(f"date - {c}_pid1@host - INFO - {m}\n" for c, m in lines)
            )
            summary = analyze(root, config(), "stages")
            self.assertEqual(summary["dropped"], 1)
            self.assertEqual(summary["delivered"], 0)
            text = (root / "analysis/events.log").read_text()
            self.assertIn("DEPART-VIA q1", text)
            self.assertNotIn("DEPART-VIA sink", text)

    def test_same_timestamp_arrival_precedes_worker_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "jobs.csv").write_text(job().raw_line + "\n")
            (root / "sink-0.csv").write_text(job().raw_line + "\n")
            # the worker log sorts before the queue log by filename
            prefix = "date - sv_pid1@host - INFO - "
            (root / "a-worker.log").write_text(
                "".join(
                    prefix + text + "\n"
                    for text in [
                        "10.000 o0.r0.near.i2 IN q0 ENTERS-SERVER 0",
                        "10.700 o0.r0.near.i2 IN q0 EXITS-SERVER 0",
                        "10.700 o0.r0.near.i2 IN q0 DEPART-VIA sink-0.csv",
                    ]
                )
            )
            (root / "q0.log").write_text(
                "date - Q_pid2@host - INFO - 10.000 o0.r0.near.i2 IN q0 ARRIVE-VIA source\n"
            )
            analyze(root, config(), "monolith")
            text = (root / "analysis/events.log").read_text()
            self.assertLess(text.index("ARRIVE-VIA"), text.index("ENTERS-SERVER"))


if __name__ == "__main__":
    unittest.main()
