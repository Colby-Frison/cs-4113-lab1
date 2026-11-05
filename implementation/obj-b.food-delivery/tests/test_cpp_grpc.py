"""Real Python/C++ gRPC integration tests; build cpp/ and install grpcio first."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from fd import OBJ_A
from lib.job import HEADER, Task, format_job_line
from run_grpc import run

try:
    import grpc
    from lib.proto_gen import job_pb2, queue_to_server_pb2_grpc
except ImportError:
    grpc = None

BINARY = APP / "cpp/build/food_server"


def request(seq=0, prep=0.001, delivery=0.002, structured=False):
    jid = f"o{seq}.r0.far.i3"
    tasks = (Task(0, prep), Task(1, delivery))
    message = job_pb2.Job(job_id=jid, arrival_time=10,
                         tasks=[job_pb2.Task(tid=t.tid, wait_seconds=t.wait_seconds) for t in tasks])
    if not structured:
        message.raw_line = format_job_line(jid, 10, tasks, wait_precision=3)
    return job_pb2.SubmitJobRequest(job=message, source_edge="q0")


@unittest.skipUnless(grpc is not None and BINARY.is_file(), "requires C++ build and Python gRPC dependencies")
class CppGrpcTests(unittest.TestCase):
    def launch(self, root, stage="monolith", peer=None):
        ready = root / "ready"
        args = [str(BINARY), "--stage", stage, "--qid", "q1" if stage == "courier" else "q0",
                "--server-id", "0", "--logfile", str(root / "server.log"), "--ready-file", str(ready)]
        args += ["--outgrpc", peer] if peer else ["--out", str(root / "sink.csv")]
        err = (root / "stderr").open("w")
        process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=err)
        def cleanup():
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            err.close()
        self.addCleanup(cleanup)
        deadline = time.monotonic() + 5
        while not ready.exists() or not ready.read_text().strip():
            if process.poll() is not None:
                self.fail((root / "stderr").read_text())
            if time.monotonic() > deadline:
                self.fail("C++ server startup timed out")
            time.sleep(0.01)
        channel = grpc.insecure_channel(f"127.0.0.1:{int(ready.read_text())}", options=[("grpc.enable_retries", 0)])
        self.addCleanup(channel.close)
        grpc.channel_ready_future(channel).result(timeout=3)
        return queue_to_server_pb2_grpc.QueueToServerStub(channel)

    def test_stage_tasks_output_and_duplicate_rejection(self):
        for stage, tids in [("kitchen", [0]), ("courier", [1]), ("monolith", [0, 1])]:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                stub = self.launch(root, stage)
                req = request()
                self.assertTrue(stub.Submit(req, timeout=3).accepted)
                self.assertEqual((root / "sink.csv").read_text(), req.job.raw_line + "\n")
                logs = (root / "server.log").read_text()
                for tid in (0, 1):
                    self.assertEqual(logs.count(f"TASK-START {tid}"), int(tid in tids))
                    self.assertEqual(logs.count(f"TASK-END {tid}"), int(tid in tids))
                self.assertIn('"W 0.00', logs)
                with self.assertRaises(grpc.RpcError) as caught:
                    stub.Submit(req, timeout=3)
                self.assertEqual(caught.exception.code(), grpc.StatusCode.ALREADY_EXISTS)
                self.assertEqual(len((root / "sink.csv").read_text().splitlines()), 1)
                self.doCleanups()

    def test_invalid_messages_do_no_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = self.launch(root)
            bad = []
            missing = job_pb2.SubmitJobRequest()
            bad.append(missing)
            for raw in ['bad', 'o0.r0.near.i1,nan,"[]"', 'o0.r0.near.i1,1,"[""0: W 0.1""]"']:
                bad.append(job_pb2.SubmitJobRequest(job=job_pb2.Job(raw_line=raw)))
            mismatch = request()
            mismatch.job.tasks[0].wait_seconds = 5
            bad.append(mismatch)
            for zone in ("north", "near\nINJECT"):
                req = request(structured=True)
                req.job.job_id = f"o0.r0.{zone}.i1"
                bad.append(req)
            for value in (-1, float("nan"), float("inf")):
                req = request(structured=True)
                req.job.tasks[0].wait_seconds = value
                bad.append(req)
            for req in bad:
                with self.assertRaises(grpc.RpcError) as caught:
                    stub.Submit(req, timeout=3)
                self.assertEqual(caught.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)
            self.assertEqual((root / "sink.csv").read_text(), "")
            self.assertEqual((root / "server.log").read_text(), "")
            self.doCleanups()

    def test_structured_only_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = self.launch(root)
            self.assertTrue(stub.Submit(request(structured=True), timeout=3).accepted)
            self.assertEqual((root / "sink.csv").read_text(), request().job.raw_line + "\n")
            self.doCleanups()

    def test_busy_worker_and_cancelled_visit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = self.launch(root)
            active = stub.Submit.future(request(prep=1), timeout=2)
            deadline = time.monotonic() + 2
            while "TASK-START" not in (root / "server.log").read_text():
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.005)
            with self.assertRaises(grpc.RpcError) as caught:
                stub.Submit(request(seq=1), timeout=1)
            self.assertEqual(caught.exception.code(), grpc.StatusCode.RESOURCE_EXHAUSTED)
            active.cancel()
            # Cancellation is polled by the server every <=10 ms.
            time.sleep(0.1)
            self.assertEqual((root / "sink.csv").read_text(), "")
            self.assertNotIn("DEPART-VIA", (root / "server.log").read_text())
            self.assertTrue(stub.Submit(request(seq=2), timeout=2).accepted)
            self.doCleanups()

    def test_downstream_failure_is_not_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = self.launch(root, "kitchen", "127.0.0.1:1")
            with self.assertRaises(grpc.RpcError):
                stub.Submit(request(), timeout=1)
            self.assertNotIn("DEPART-VIA", (root / "server.log").read_text())
            self.doCleanups()

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
                    self.assertTrue(all(c["status"] == "available" for c in state["couriers"].values()))

    def test_pickup_overflow_is_accounted_as_drop(self):
        config = json.loads((APP / "topology/baseline.json").read_text())
        def fast_kitchen(out, **kwargs):
            out.write(HEADER + "\n")
            for seq in range(20):
                out.write(request(seq=seq, prep=0, delivery=0.1).job.raw_line + "\n")
                out.flush()
        with tempfile.TemporaryDirectory() as tmp, patch("run_grpc.generate", fast_kitchen):
            summary = run(tmp, {**config, "kitchen_capacity": 30, "pickup_capacity": 1, "couriers": 1}, timeout=10)
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
