"""Check in-memory visit counts and the existing queue gRPC interfaces."""

import csv
import io
import json
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import grpc

OBJ_A = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(OBJ_A))
from lib.proto_gen import job_pb2
from lib.proto_gen import queue_to_server_pb2_grpc as server_rpc


def binary(directory, name):
    build = OBJ_A / directory / "build"
    for folder in (build, build / "Release", build / "Debug"):
        for filename in (name, f"{name}.exe"):
            path = folder / filename
            if path.is_file():
                return [str(path)]
    raise FileNotFoundError(f"build {directory} first")


def job_row(key="j0", wait=0):
    output = io.StringIO()
    csv.writer(output, lineterminator="\n").writerow(
        [key, "0.000", json.dumps([f"0: W {wait:.6f}"])]
    )
    return output.getvalue().strip()


class KvPortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = binary("server-func-stateful-cpp", "stateful_server")
        cls.queue = binary("queue-cpp", "queue_server")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def read_counts(self, log_path):
        return {
            key: int(count)
            for key, count in re.findall(r"state-visits (\S+) (\d+)", log_path.read_text())
        }

    def stop(self, process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def start_service(self, name, command, extra=()):
        log = self.root / f"{name}.log"
        error = (self.root / f"{name}.err").open("w")
        self.addCleanup(error.close)
        # port zero avoids fixed-port conflicts between test runs
        process = subprocess.Popen(
            [*command, "--ingrpc", "127.0.0.1:0,2", "--logfile", str(log), *extra],
            stdout=subprocess.DEVNULL,
            stderr=error,
        )
        self.addCleanup(self.stop, process)
        end = time.monotonic() + 15
        while time.monotonic() < end:
            text = log.read_text() if log.exists() else ""
            match = re.search(r"gRPC listen (127\.0\.0\.1:\d+)", text)
            if match:
                channel = grpc.insecure_channel(match[1])
                self.addCleanup(channel.close)
                grpc.channel_ready_future(channel).result(timeout=5)
                return process, channel, match[1]
            if process.poll() is not None:
                self.fail((self.root / f"{name}.err").read_text() + text)
            time.sleep(0.02)
        self.fail(f"{name} did not start: {log.read_text() if log.exists() else ''}")

    def test_file_visits_reset_between_runs(self):
        rows = [job_row(), job_row("j1"), job_row()]
        jobs = self.root / "jobs.csv"
        jobs.write_text("Job Id,arrival time,Tasks\n" + "\n".join(rows) + "\n")
        args = [
            *self.server,
            "-i",
            str(jobs),
            "-o",
            str(self.root / "sink.csv"),
            "--logfile",
            str(self.root / "server.log"),
        ]
        subprocess.run(args, check=True, capture_output=True, timeout=15)
        self.assertEqual(self.read_counts(self.root / "server.log"), {"j0": 2, "j1": 1})
        self.assertEqual((self.root / "sink.csv").read_text().splitlines(), rows)
        log = (self.root / "server.log").read_text()
        for event in ("ENTERS-SERVER", "EXITS-SERVER", "TASK-START", "TASK-END", "DEPART-VIA"):
            self.assertEqual(log.count(event), 3)
        # a new process starts with an empty map
        (self.root / "server.log").unlink()
        subprocess.run(args, check=True, capture_output=True, timeout=15)
        self.assertEqual(self.read_counts(self.root / "server.log"), {"j0": 2, "j1": 1})

    def test_concurrent_grpc_pipeline(self):
        sink = self.root / "sink.csv"
        _, _, destination = self.start_service(
            "sink",
            [sys.executable, str(OBJ_A / "queue/queue.py")],
            ["-o", str(sink), "--qsize", "100"],
        )
        server, channel, address = self.start_service(
            "server", self.server, ["--outgrpc", f"{destination},2"]
        )
        rows = [job_row(wait=0.02)] * 20
        jobs = self.root / "jobs.csv"
        jobs.write_text("\n".join(rows) + "\n")
        subprocess.run(
            [*self.queue, "-i", str(jobs), "--outgrpc", f"{address},8", "--qsize", "100"],
            check=True,
            capture_output=True,
            timeout=20,
        )
        stub = server_rpc.QueueToServerStub(channel)
        job = job_pb2.Job(job_id="structured", tasks=[job_pb2.Task(tid=0, wait_seconds=0)])
        self.assertTrue(stub.Submit(job_pb2.SubmitJobRequest(job=job), timeout=5).accepted)
        with self.assertRaises(grpc.RpcError) as error:
            stub.Submit(job_pb2.SubmitJobRequest(job=job_pb2.Job(raw_line="broken")), timeout=5)
        self.assertEqual(error.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)
        end = time.monotonic() + 5
        while time.monotonic() < end:
            if sink.exists() and len(sink.read_text().splitlines()) == 21:
                break
            time.sleep(0.02)
        self.assertCountEqual(sink.read_text().splitlines(), rows + [job_row("structured")])
        # overlapping event pairs show whether the two-worker limit was respected
        active = peak = 0
        for line in (self.root / "server.log").read_text().splitlines():
            if "ENTERS-SERVER" in line:
                active += 1
                peak = max(peak, active)
            elif "EXITS-SERVER" in line:
                active -= 1
        self.assertEqual((active, peak), (0, 2))
        # Unix termination drains visits and writes the final in-memory counts
        if sys.platform != "win32":
            self.stop(server)
            self.assertEqual(server.returncode, 0)
            self.assertEqual(
                self.read_counts(self.root / "server.log"), {"j0": 20, "structured": 1}
            )


if __name__ == "__main__":
    unittest.main()
