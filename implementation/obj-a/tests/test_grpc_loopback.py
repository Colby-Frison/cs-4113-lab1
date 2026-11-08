"""Localhost gRPC: queue ingress drops when full, server function runs the visit."""

from __future__ import annotations

import unittest

from support import ROOT, capture_logger, event_kinds, load_script

try:
    from lib.grpc_runtime import start_queue_server, start_server_func_server, submit_job
    from lib.grpc_spec import ListenSpec, parse_dial
except ImportError:
    start_queue_server = None

from lib.job import Task, format_job_line, iter_jobs, parse_job_line
from lib.server import serve_job

QUEUE = load_script(ROOT / "queue" / "queue.py", "obj_a_queue_grpc")


def _job(job_id: str):
    return parse_job_line(format_job_line(job_id, 1.0, (Task(0, 0.0),)))


@unittest.skipIf(start_queue_server is None, "grpcio is not installed")
class GrpcLoopbackTests(unittest.TestCase):
    def test_queue_ingress_accepts_then_drops(self) -> None:
        log, messages = capture_logger("loop-queue")
        buffer = QUEUE.Queue(maxsize=1)

        def admit(job, source: str) -> bool:
            return QUEUE.admit_job(buffer, job, "q0", source, log)

        server, port = start_queue_server(ListenSpec("127.0.0.1", 0, 2), admit)
        try:
            from_server = parse_dial(f"127.0.0.1:{port},1", caller="server")
            from_queue = parse_dial(f"127.0.0.1:{port},1,queue", caller="queue")
            self.assertEqual(submit_job(from_server, _job("j0"), "sf0"), "accepted")
            self.assertEqual(submit_job(from_queue, _job("j1"), "q1"), "rejected")
        finally:
            server.stop(0).wait(3)
        held = buffer.get_nowait()
        self.assertEqual(held.job_id, "j0")
        self.assertTrue(buffer.empty())
        self.assertTrue(any("j0 IN q0 ARRIVE-VIA sf0" in message for message in messages))
        self.assertTrue(any("j1 IN q0 ERROR QUEUE-FULL" in message for message in messages))
        self.assertFalse(any("ARRIVE-VIA q1" in message for message in messages))

    def test_server_function_visit_over_grpc(self) -> None:
        import tempfile
        from pathlib import Path

        log, messages = capture_logger("loop-sf")
        with tempfile.TemporaryDirectory() as tmp:
            dest = str(Path(tmp) / "sink.csv")
            slept: list[float] = []
            sent = _job("j0")

            def visit(job) -> None:
                serve_job(job, [dest], "q0", 0, log, sleep_fn=slept.append)

            server, port = start_server_func_server(ListenSpec("127.0.0.1", 0, 2), visit)
            try:
                dial = parse_dial(f"127.0.0.1:{port},1", caller="queue")
                self.assertEqual(dial.service, "queue_to_server")
                self.assertEqual(submit_job(dial, sent, "qIn"), "accepted")
            finally:
                server.stop(0).wait(3)
            delivered = list(iter_jobs(Path(dest).read_text(encoding="utf-8").splitlines()))
        self.assertEqual([job.raw_line for job in delivered], [sent.raw_line])
        self.assertEqual(slept, [0.0])
        self.assertEqual(
            event_kinds(messages),
            ["ENTERS-SERVER", "TASK-START", "TASK-END", "EXITS-SERVER", "DEPART-VIA"],
        )
        self.assertIn("DEPART-VIA sink.csv", messages[-1])


if __name__ == "__main__":
    unittest.main()
