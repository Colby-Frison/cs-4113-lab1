"""Queue admission: accept when there is room, drop when the buffer is full."""

from __future__ import annotations

import unittest

from support import ROOT, capture_logger, load_script

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
        self.assertEqual(buffer.qsize(), 1)
        self.assertEqual(messages[-1], "11.000 j1 IN q0 ERROR QUEUE-FULL")

    def test_grpc_flag_is_rejected(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            QUEUE.main(["--ingrpc", "0.0.0.0,1", "-i", "in.csv", "-o", "out.csv"])
        self.assertIn("not implemented", str(raised.exception).lower())


if __name__ == "__main__":
    unittest.main()
