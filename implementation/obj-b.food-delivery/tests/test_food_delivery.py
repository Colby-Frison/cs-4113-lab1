import csv
import io
import json
import logging
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from fd.order import Order, validate_order
from fd.workload import generate
from fd.server import serve
from fd.analysis import analyze
from fd.dispatch import Dispatcher
from lib.job import Task, format_job_line, parse_job_line, iter_jobs
from run_demo import run, validate_config


def job():
    return parse_job_line(format_job_line(Order(0, 0, "near", 2).job_id, 10,
                                         (Task(0, 0.2), Task(1, 0.5))))


def config():
    return {**json.loads((APP / "topology/baseline.json").read_text()),
            "count": 8, "time_scale": 0.1, "arrival_mean": 0.01}


class FoodDeliveryTests(unittest.TestCase):
    def test_stage_entrypoints_match_monolith(self):
        """Separately launched services do each task once and preserve the job."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "orders.csv"
            original = format_job_line(Order(1, 0, "far", 3).job_id, 10,
                                       (Task(0, 0.001), Task(1, 0.002)), wait_precision=3)
            source.write_text(original + "\n")
            for script, name, incoming, outgoing, qid in [
                ("server-stages/kitchen.py", "kitchen", source, root / "prepared.csv", "q0"),
                ("server-stages/courier.py", "courier", root / "prepared.csv", root / "delivered.csv", "q1"),
                ("server-monolith/server_func.py", "monolith", source, root / "mono.csv", "q0"),
            ]:
                result = subprocess.run([sys.executable, str(APP / script), "-i", str(incoming),
                    "-o", str(outgoing), "--qid", qid, "--logfile", str(root / f"{name}.log")],
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(outgoing.read_text(), original + "\n")
            kitchen = (root / "kitchen.log").read_text()
            courier = (root / "courier.log").read_text()
            monolith = (root / "monolith.log").read_text()
            self.assertNotIn("TASK-START 1", kitchen)
            self.assertNotIn("TASK-START 0", courier)
            for tid in (0, 1):
                self.assertEqual((kitchen + courier).count(f"TASK-START {tid}"), 1)
                self.assertEqual(monolith.count(f"TASK-START {tid}"), 1)

    def test_invalid_request_has_no_work_or_output(self):
        invalid = parse_job_line(format_job_line(Order(0, 1, "near", 2).job_id, 10, job().tasks))
        for stage in ("kitchen", "courier", "monolith"):
            sleeps, out, messages = [], io.StringIO(), io.StringIO()
            log = logging.Logger(stage)
            log.addHandler(logging.StreamHandler(messages))
            with self.assertRaisesRegex(ValueError, "restaurant r0"):
                serve(invalid, stage, "q0", 0, out, "sink", log, sleep=sleeps.append)
            self.assertEqual(sleeps, [])
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(messages.getvalue(), "")

    def test_failed_output_does_not_report_completion(self):
        class FailedWriter(io.StringIO):
            def flush(self):
                raise OSError("sink unavailable")
        messages = io.StringIO()
        log = logging.Logger("failed-output")
        log.addHandler(logging.StreamHandler(messages))
        with self.assertRaisesRegex(OSError, "sink unavailable"):
            serve(job(), "courier", "q1", 0, FailedWriter(), "sink", log, sleep=lambda _: None)
        self.assertNotIn("DEPART-VIA", messages.getvalue())
        self.assertNotIn("courier-released", messages.getvalue())

    def test_dispatch_rejects_double_assignment_and_wrong_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            dispatch = Dispatcher(1, 2, tmp)
            try:
                dispatch.admit(job(), "prepared")
                self.assertEqual(dispatch.assign_next(0), job())
                with self.assertRaisesRegex(ValueError, "not available"):
                    dispatch.assign_next(0)
                with self.assertRaisesRegex(ValueError, "no active assignment"):
                    dispatch.release(0, "wrong-order")
                self.assertEqual(dispatch.couriers[0]["status"], "busy")
                self.assertEqual(dispatch.couriers[0]["completed"], 0)
                dispatch.release(0, job().job_id)
                with self.assertRaisesRegex(ValueError, "no active assignment"):
                    dispatch.release(0, job().job_id)
                self.assertEqual(dispatch.couriers[0]["completed"], 1)
            finally:
                for handler in dispatch.log.handlers:
                    handler.close()

    def test_dispatch_registry_capacity_and_release(self):
        started, finish = threading.Event(), threading.Event()
        def delivery_wait(_):
            started.set()
            if not finish.wait(3):
                raise RuntimeError("test delivery timed out")
        with tempfile.TemporaryDirectory() as tmp:
            dispatch = Dispatcher(1, 1, tmp, sleep=delivery_wait)
            worker = threading.Thread(target=dispatch.worker, args=(0,))
            first = job()
            second = parse_job_line(format_job_line(Order(1, 0, "near", 2).job_id, 10, first.tasks))
            third = parse_job_line(format_job_line(Order(2, 0, "near", 2).job_id, 10, first.tasks))
            try:
                dispatch.admit(first, "prepared")
                worker.start()
                self.assertTrue(started.wait(3))
                with dispatch.condition:
                    self.assertEqual(dispatch.couriers[0]["status"], "busy")
                    self.assertEqual(dispatch.couriers[0]["order_id"], first.job_id)
                self.assertTrue(dispatch.admit(second, "prepared"))
                self.assertFalse(dispatch.admit(third, "prepared"))
                state = json.loads((Path(tmp) / "dispatch-state.json").read_text())
                self.assertEqual(state["waiting"], [second.job_id])
                self.assertEqual(state["couriers"]["0"]["order_id"], first.job_id)
                finish.set()
                with dispatch.condition:
                    self.assertTrue(dispatch.condition.wait_for(lambda: dispatch.couriers[0]["completed"] == 2, timeout=3))
                    self.assertEqual(dispatch.couriers[0]["status"], "available")
                    self.assertIsNone(dispatch.couriers[0]["order_id"])
                delivered = list(iter_jobs((Path(tmp) / "sink-0.csv").read_text().splitlines()))
                self.assertEqual([j.job_id for j in delivered], [first.job_id, second.job_id])
            finally:
                finish.set()
                with dispatch.condition:
                    dispatch.stopping = True
                    dispatch.condition.notify_all()
                if worker.ident is not None:
                    worker.join(3)
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

    def test_order_round_trip_and_required_tasks(self):
        self.assertEqual(validate_order(job()), Order(0, 0, "near", 2))
        for zone in ("near", "mid", "far"):
            order = Order(3, 0, zone, 2)
            self.assertEqual(validate_order(parse_job_line(format_job_line(order.job_id, 10, job().tasks))), order)
        for jid, tasks in [("invalid", (Task(0, 1), Task(1, 1))),
                           (job().job_id, (Task(1, 1), Task(0, 1)))]:
            with self.assertRaises(ValueError):
                validate_order(parse_job_line(format_job_line(jid, 1, tasks)))

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
            task_strings = json.loads(next(csv.reader([generated.raw_line]))[2])
            for token in task_strings:
                self.assertRegex(token, r"^\d+: W \d+\.\d{3}$")
        self.assertTrue(all(w > 0 for w in waits))
        scaled = io.StringIO()
        generate(scaled, count=4, seed=4, time_scale=0.5, sleep=lambda _: None, clock=lambda: 10)
        for a, b in zip(iter_jobs(first.getvalue().splitlines()), iter_jobs(scaled.getvalue().splitlines())):
            self.assertEqual(a.job_id, b.job_id)
            for ta, tb in zip(a.tasks, b.tasks):
                # Each generated duration is independently rounded to milliseconds.
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
                self.assertNotIn("W 0.200000", events)
                self.assertNotIn("W 0.500000", events)
                for duration in expected:
                    self.assertIn(f'"W {duration:.3f}"', events)
                self.assertLess(events.index("ENTERS-SERVER"), events.index("EXITS-SERVER"))
                if stage == "courier":
                    self.assertLess(events.index("courier-assigned"), events.index("TASK-START"))
                    self.assertLess(events.index("TASK-END"), events.index("courier-released"))

    def test_rejects_invalid_config(self):
        for key, value in [("couriers", 0), ("kitchen_workers", True), ("time_scale", float("nan")),
                           ("arrival_mean", -1), ("seed", 1.5)]:
            with self.assertRaises(ValueError):
                validate_config({**config(), key: value})

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
                result = subprocess.run([sys.executable, str(APP / "run_demo.py"),
                    "--count", "8", "--time-scale", "0.1", "--mode", mode,
                    "--workdir", tmp], capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                summary = json.loads((Path(tmp) / "analysis/summary.json").read_text())
                self.assertEqual(summary["generated"], 8)
                self.assertEqual(summary["delivered"], 8)
                self.assertEqual(summary["dropped"], 0)
                events = (Path(tmp) / "analysis/events.log").read_text()
                self.assertEqual(events.count("DEPART-VIA sink"), 8)
                self.assertEqual(events.count("TASK-START 0"), 8)
                self.assertEqual(events.count("TASK-START 1"), 8)
                for path in Path(tmp).glob("*.log"):
                    self.assertNotIn("_pid", path.read_text())
                    self.assertNotIn("@", path.read_text())
                if mode == "stages":
                    state = json.loads((Path(tmp) / "dispatch-state.json").read_text())
                    self.assertEqual(state["waiting"], [])
                    self.assertEqual(sum(c["completed"] for c in state["couriers"].values()), 8)
                    self.assertTrue(all(c["status"] == "available" for c in state["couriers"].values()))
                # Each courier is a serial resource, never assigned twice concurrently.
                for path in Path(tmp).glob("courier-*.log"):
                    busy = False
                    for line in path.read_text().splitlines():
                        if "courier-assigned" in line:
                            self.assertFalse(busy)
                            busy = True
                        if "courier-released" in line:
                            self.assertTrue(busy)
                            busy = False
                    self.assertFalse(busy)

    def test_timeout_reaps_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TimeoutError):
                run(Path(tmp), {**config(), "time_scale": 100}, timeout=0.01)

    def test_downstream_drop_is_not_mistaken_for_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "jobs.csv").write_text(job().raw_line + "\n")
            lines = [
                ("Q", "10.000 o0.r0.near.i2 IN q0 ARRIVE-VIA source"),
                ("sv", "10.010 o0.r0.near.i2 IN q0 ENTERS-SERVER 0"),
                ("sv", "10.210 o0.r0.near.i2 IN q0 EXITS-SERVER 0"),
                ("sv", "10.211 o0.r0.near.i2 IN q0 DEPART-VIA prepared-0"),
                ("Q", "10.212 o0.r0.near.i2 IN q1 ERROR QUEUE-FULL")]
            (root / "events.log").write_text("".join(f"date - {c}_pid1@host - INFO - {m}\n" for c,m in lines))
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
            # The worker log sorts before the queue log by filename.
            prefix = "date - sv_pid1@host - INFO - "
            (root / "a-worker.log").write_text("".join(prefix + text + "\n" for text in [
                "10.000 o0.r0.near.i2 IN q0 ENTERS-SERVER 0",
                "10.700 o0.r0.near.i2 IN q0 EXITS-SERVER 0",
                "10.700 o0.r0.near.i2 IN q0 DEPART-VIA sink-0.csv"]))
            (root / "q0.log").write_text("date - Q_pid2@host - INFO - 10.000 o0.r0.near.i2 IN q0 ARRIVE-VIA source\n")
            analyze(root, config(), "monolith")
            text = (root / "analysis/events.log").read_text()
            self.assertLess(text.index("ARRIVE-VIA"), text.index("ENTERS-SERVER"))


if __name__ == "__main__":
    unittest.main()
