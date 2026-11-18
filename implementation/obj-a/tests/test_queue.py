"""Queue admission: accept when there is room, drop when the buffer is full."""

from __future__ import annotations

import unittest

from support import ROOT, capture_logger, event_kinds, load_script

from lib.grpc_spec import parse_dial
from lib.job import Job, Task, format_job_line, parse_job_line

QUEUE = load_script(ROOT / "queue" / "queue.py", "obj_a_queue")


def _job(job_id: str) -> Job:
    return parse_job_line(format_job_line(job_id, 1.0, (Task(0, 0.25),)))


class AdmitTests(unittest.TestCase):
    def test_free_buffer_logs_arrive(self) -> None:
        log, messages = capture_logger("admit-free")
        buffer = QUEUE.Queue(maxsize=1)
        accepted = QUEUE.admit_job(buffer, _job("j0"), "q0", "inputs/in0.csv", log, now=10)
        self.assertTrue(accepted)
        self.assertEqual(messages, ["10.000 j0 IN q0 ARRIVE-VIA in0.csv"])
        self.assertEqual(buffer.qsize(), 1)

    def test_full_buffer_logs_queue_full_and_drops(self) -> None:
        log, messages = capture_logger("admit-full")
        buffer = QUEUE.Queue(maxsize=1)
        self.assertTrue(QUEUE.admit_job(buffer, _job("j0"), "q0", "in0.csv", log, now=10))
        dropped = QUEUE.admit_job(buffer, _job("j1"), "q0", "in0.csv", log, now=11)
        self.assertFalse(dropped)
        self.assertEqual(
            messages,
            [
                "10.000 j0 IN q0 ARRIVE-VIA in0.csv",
                "11.000 j1 IN q0 ERROR QUEUE-FULL",
            ],
        )
        self.assertEqual(buffer.get().job_id, "j0")

    def test_ingrpc_without_a_port_is_rejected(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            QUEUE.main(["--ingrpc", "0.0.0.0,1", "-o", "out.csv"])
        self.assertIn("port", str(raised.exception).lower())

    def test_sender_logs_depart_when_the_peer_rejects(self) -> None:
        buffer = QUEUE.Queue()
        buffer.put(_job("j1"))
        buffer.put(None)
        _log, messages = capture_logger("obj_a_queue")
        seen: list[str] = []

        def submit(dial, job, source, logger=None):
            seen.append(f"{job.job_id} {source} {dial.address}")
            return "rejected"

        QUEUE.worker_grpc_sender(buffer, parse_dial("127.0.0.1:9,1", caller="queue"), "q0", submit)
        self.assertEqual(seen, ["j1 q0 127.0.0.1:9"])
        self.assertEqual(event_kinds(messages), ["DEPART-VIA"])
        self.assertTrue(any("j1 IN q0 DEPART-VIA 127.0.0.1:9" in message for message in messages))

    def test_sender_skips_depart_when_the_transport_fails(self) -> None:
        buffer = QUEUE.Queue()
        buffer.put(_job("j1"))
        buffer.put(None)
        _log, messages = capture_logger("obj_a_queue")

        def submit(dial, job, source, logger=None):
            return "failed"

        QUEUE.worker_grpc_sender(buffer, parse_dial("127.0.0.1:9,1", caller="queue"), "q0", submit)
        self.assertEqual(event_kinds(messages), [])

import tempfile
from pathlib import Path


class QueueExecutionTests(unittest.TestCase):

    def test_complete_queue_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            infile = Path(tmp) / "input.csv"
            outfile = Path(tmp) / "output.csv"

            job = _job("j0")
            infile.write_text(job.raw_line + "\n")

            result = QUEUE.main([
                "--qid", "q0",
                "--qsize", "10",
                "--infile", str(infile),
                "--outfile", str(outfile),
            ])

            self.assertEqual(result, 0)
            self.assertEqual(
                outfile.read_text().strip(),
                job.raw_line,
            )

    def test_invalid_queue_size(self):
        with self.assertRaises(SystemExit):
            QUEUE.main([
                "--qsize", "0",
                "-i", "input.csv",
                "-o", "output.csv",
            ])

    def test_missing_input(self):
        with self.assertRaises(SystemExit):
            QUEUE.main(["-o", "output.csv"])

    def test_missing_output(self):
        with self.assertRaises(SystemExit):
            QUEUE.main(["-i", "input.csv"])

if __name__ == "__main__":
    unittest.main()
