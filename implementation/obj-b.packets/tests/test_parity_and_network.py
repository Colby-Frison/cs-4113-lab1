"""Real processes: whole networks, and the Python and C++ servers compared.

Skipped without grpcio. The C++ cases also need ``PACKET_SERVER_BIN`` set to a built
``packet_server``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from support import APP, event_kinds

try:
    import grpc  # noqa: F401

    from lib.grpc_runtime import start_queue_server, submit_job
    from lib.grpc_spec import parse_dial, parse_listen

    HAVE_GRPC = True
except ImportError:
    HAVE_GRPC = False

from lib.job import Job, Task, format_job_line, ipv4_to_int, parse_job_line

CPP_SERVER = os.environ.get("PACKET_SERVER_BIN")
MONOLITH = APP / "server-monolith" / "server_func.py"
STAGES = APP / "server-stages" / "server_func.py"
RUN_LOCAL = APP / "run_local.py"
BODY = re.compile(r"^\S+ \S+ - sv_pid\d+@\S+ - (INFO|ERROR) - (.*)$")
TASKS = (Task(0, 0.002), Task(1, 0.002), Task(2, 0.002))


def packet(job_id: str, dst: str, ttl: int, next_hop: str = "", tasks=TASKS) -> Job:
    job = parse_job_line(format_job_line(job_id, 1.0, tasks, dst_ip=ipv4_to_int(dst), ttl=ttl))
    return Job(job.job_id, job.arrival_time, job.tasks, job.raw_line, job.dst_ip, job.ttl, next_hop)


class CapturingQueue:
    """Stands in for the next queue and records what reaches it."""

    def __init__(self, port: int) -> None:
        self.jobs: list[tuple[Job, str]] = []
        self._lock = threading.Lock()
        self.server, self.port = start_queue_server(parse_listen(f"127.0.0.1:{port},4"), self._admit)

    def _admit(self, job: Job, source: str) -> bool:
        with self._lock:
            self.jobs.append((job, source))
        return True

    def stop(self) -> None:
        self.server.stop(0)


class ServerProcess:
    def __init__(self, command: list[str], directory: Path, name: str) -> None:
        self.log = directory / f"{name}.log"
        self.stderr = (directory / f"{name}.stderr").open("w")
        self.process = subprocess.Popen(
            [*command, "--logfile", str(self.log)], stdout=self.stderr, stderr=self.stderr, cwd=directory
        )
        deadline = time.time() + 20
        while not (self.log.exists() and "gRPC listen" in self.log.read_text()):
            if self.process.poll() is not None or time.time() > deadline:
                raise RuntimeError(f"{name} did not start: {(directory / f'{name}.stderr').read_text()}")
            time.sleep(0.02)

    def stop(self) -> int:
        self.process.send_signal(2)
        status = self.process.wait(timeout=10)
        self.stderr.close()
        return status

    def bodies(self) -> list[str]:
        lines = []
        for line in self.log.read_text().splitlines():
            match = BODY.match(line)
            if match is None:
                raise AssertionError(f"log prefix does not match: {line!r}")
            lines.append(match.group(2))
        return lines


def normalized(bodies: list[str]) -> list[str]:
    """Event lines without the wall-clock time."""
    return [body.split(" ", 1)[1] for body in bodies if " IN " in body]


@unittest.skipUnless(HAVE_GRPC, "grpcio is not installed")
class NetworkTests(unittest.TestCase):
    def run_network(self, *extra: str) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(RUN_LOCAL), "--out", directory + "/run", "--duration", "2", *extra],
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            summary = json.loads((Path(directory) / "run" / "summary.json").read_text())
            sent = sink_rows = 0
            for path in (Path(directory) / "run").glob("jobs-*.csv"):
                sent += len(path.read_text().splitlines()) - 1
            for path in (Path(directory) / "run").glob("sink-*.csv"):
                sink_rows += len(path.read_text().splitlines())
            self.assertEqual(sent, summary["sent"])
            self.assertEqual(sink_rows, summary["delivered"])
            return summary

    def check_all_delivered(self, summary: dict) -> None:
        self.assertGreater(summary["sent"], 0)
        self.assertEqual(summary["delivered"], summary["sent"])
        self.assertEqual(summary["TTL-EXPIRED"], 0)
        self.assertEqual(summary["NO-OUT-EDGE"], 0)

    def test_python_monolith_network_delivers_everything(self) -> None:
        self.check_all_delivered(self.run_network("--routers", "5", "--sources", "2", "--rate", "8"))

    def test_python_stage_network_delivers_everything(self) -> None:
        self.check_all_delivered(
            self.run_network("--mode", "stages", "--routers", "3", "--sources", "2", "--rate", "6")
        )

    def test_overload_drops_are_counted_and_every_packet_is_accounted_for(self) -> None:
        summary = self.run_network("--routers", "4", "--sources", "2", "--rate", "150")
        self.assertGreater(summary["QUEUE-FULL"], 0)
        self.assertEqual(summary["delivered"] + summary["QUEUE-FULL"], summary["sent"])

    @unittest.skipUnless(CPP_SERVER, "PACKET_SERVER_BIN is not set")
    def test_cpp_monolith_network_delivers_everything(self) -> None:
        self.check_all_delivered(
            self.run_network("--routers", "5", "--sources", "2", "--rate", "8", "--server-bin", CPP_SERVER)
        )

    @unittest.skipUnless(CPP_SERVER, "PACKET_SERVER_BIN is not set")
    def test_cpp_stage_network_delivers_everything(self) -> None:
        self.check_all_delivered(
            self.run_network(
                "--mode", "stages", "--routers", "3", "--sources", "2", "--rate", "6",
                "--server-bin", CPP_SERVER,
            )
        )


@unittest.skipUnless(HAVE_GRPC and CPP_SERVER, "needs grpcio and PACKET_SERVER_BIN")
class ParityTests(unittest.TestCase):
    """Send the same packets to both servers. Logs and forwarded fields must match."""

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.directory = Path(self._directory.name)
        self.addCleanup(self._directory.cleanup)
        (self.directory / "routes.tsv").write_text(
            "10.0.0.0/24\tlocal\n10.0.1.0/24\tq1\n10.0.9.0/24\tq99\n10.0.0.0/8\tq1\n"
        )

    def launch(self, name: str, stage: str, port: int, *flags: str, workers: int = 1) -> ServerProcess:
        command = (
            [CPP_SERVER, "--stage", stage]
            if name.startswith("cpp")
            else (
                [sys.executable, str(MONOLITH)]
                if stage == "monolith"
                else [sys.executable, str(STAGES), "--stage", stage]
            )
        )
        return ServerProcess(
            [
                *command, "--qid", "q5", "--server-id", "3",
                "--ingrpc", f"127.0.0.1:{port},{workers}",
                "--addresses", "addresses.csv", *flags,
            ],
            self.directory,
            name,
        )

    def exchange(self, stage: str, packets: list[Job], flags: list[str], base_port: int) -> dict:
        """Run the packets through each language. Returns what each one did."""
        results = {}
        for offset, language in enumerate(("python", "cpp")):
            queue_port = base_port + offset * 2
            server_port = queue_port + 1
            (self.directory / "addresses.csv").write_text(
                "qid,host,queue_port,server_port\n"
                f"q1,127.0.0.1,{queue_port},0\nq7,127.0.0.1,{queue_port},0\n"
            )
            sink_flags = [
                value.replace("SINK", f"sink-{language}.csv") for value in flags
            ]
            queue = CapturingQueue(queue_port)
            server = self.launch(language, stage, server_port, *sink_flags)
            try:
                dial = parse_dial(f"127.0.0.1:{server_port},1", caller="queue")
                outcomes = [submit_job(dial, job, "q4") for job in packets]
            finally:
                status = server.stop()
                queue.stop()
            sink = self.directory / f"sink-{language}.csv"
            results[language] = {
                "status": status,
                "outcomes": outcomes,
                "events": normalized(server.bodies()),
                "forwarded": [
                    (job.job_id, job.dst_ip, job.ttl, job.next_hop, job.raw_line, source)
                    for job, source in queue.jobs
                ],
                "sink": sink.read_text() if sink.exists() else "",
            }
        return results

    def assert_same(self, results: dict) -> dict:
        python, cpp = results["python"], results["cpp"]
        self.assertEqual(python["status"], 0)
        self.assertEqual(cpp["status"], 0)
        self.assertEqual(python["outcomes"], cpp["outcomes"])
        self.assertEqual(python["forwarded"], cpp["forwarded"])
        self.assertEqual(python["sink"], cpp["sink"])
        # The sink's own name differs by language in this test only.
        def clean(events):
            return [re.sub(r"sink-(python|cpp)\.csv", "sink.csv", e) for e in events]
        def ports(events):
            return [re.sub(r"127\.0\.0\.1:\d+", "NEXT", e) for e in clean(events)]
        self.assertEqual(ports(python["events"]), ports(cpp["events"]))
        return python

    def test_monolith_matches_on_every_outcome(self) -> None:
        packets = [
            packet("p-local", "10.0.0.5", 64),
            packet("p-next", "10.0.1.5", 64),
            packet("p-coarse", "10.200.1.5", 64),
            packet("p-ttl1", "10.0.1.5", 1),
            packet("p-ttl0", "10.0.1.5", 0),
            packet("p-ttl1-local", "10.0.0.5", 1),
            packet("p-noroute", "192.168.0.1", 64),
            packet("p-noaddress", "10.0.9.1", 64),
        ]
        python = self.assert_same(
            self.exchange("monolith", packets, ["--routes", "routes.tsv", "--sink", "SINK"], 17000)
        )
        errors = [event for event in python["events"] if " ERROR " in event]
        self.assertEqual(
            sorted(errors),
            [
                "p-noaddress IN q5 ERROR NO-OUT-EDGE",
                "p-noroute IN q5 ERROR NO-OUT-EDGE",
                "p-ttl0 IN q5 ERROR TTL-EXPIRED",
                "p-ttl1 IN q5 ERROR TTL-EXPIRED",
            ],
        )
        self.assertEqual(len(python["forwarded"]), 2)
        self.assertEqual({entry[2] for entry in python["forwarded"]}, {63})
        self.assertEqual(python["sink"].count("\n"), 2)

    def test_validate_matches(self) -> None:
        packets = [packet("p-ok", "10.0.1.5", 5), packet("p-bad", "10.0.1.5", 0)]
        python = self.assert_same(self.exchange("validate", packets, ["--next-qid", "q7"], 17010))
        self.assertEqual([entry[0] for entry in python["forwarded"]], ["p-ok"])

    def test_lookup_matches(self) -> None:
        packets = [
            packet("p-next", "10.0.1.5", 5),
            packet("p-local", "10.0.0.5", 5),
            packet("p-none", "192.168.0.1", 5),
        ]
        python = self.assert_same(
            self.exchange("lookup", packets, ["--routes", "routes.tsv", "--next-qid", "q7"], 17020)
        )
        self.assertEqual({entry[3] for entry in python["forwarded"]}, {"q1", "local"})
        self.assertEqual({entry[2] for entry in python["forwarded"]}, {5}, "lookup keeps the TTL")

    def test_forward_matches(self) -> None:
        packets = [
            packet("p-next", "10.0.1.5", 5, next_hop="q1"),
            packet("p-local", "10.0.0.5", 5, next_hop="local"),
            packet("p-expired", "10.0.1.5", 1, next_hop="q1"),
            packet("p-noaddress", "10.0.9.1", 5, next_hop="q99"),
        ]
        python = self.assert_same(self.exchange("forward", packets, ["--sink", "SINK"], 17030))
        self.assertEqual(python["forwarded"][0][2:4], (4, ""))

    def test_both_languages_limit_visits_to_the_server_count(self) -> None:
        slow = (Task(0, 0.25), Task(1, 0.0), Task(2, 0.0))
        for offset, language in enumerate(("python", "cpp")):
            with self.subTest(language):
                port = 17040 + offset
                (self.directory / "addresses.csv").write_text("qid,host,queue_port,server_port\n")
                server = self.launch(
                    language, "monolith", port, "--routes", "routes.tsv",
                    "--sink", f"s-{language}.csv", workers=2,
                )
                dial = parse_dial(f"127.0.0.1:{port},1", caller="queue")
                threads = [
                    threading.Thread(
                        target=submit_job,
                        args=(dial, packet(f"p{index}", "10.0.0.5", 5, tasks=slow), "q4"),
                    )
                    for index in range(6)
                ]
                started = time.time()
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
                elapsed = time.time() - started
                server.stop()
                peak = in_service = 0
                for kind in event_kinds(server.bodies()):
                    if kind == "ENTERS-SERVER":
                        in_service += 1
                        peak = max(peak, in_service)
                    elif kind == "EXITS-SERVER":
                        in_service -= 1
                self.assertEqual(peak, 2)
                self.assertGreaterEqual(elapsed, 0.7, "6 visits of 0.25 s on 2 servers")


if __name__ == "__main__":
    unittest.main()
