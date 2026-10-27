"""Lab 0 event message shape."""

from __future__ import annotations

import unittest

import support  # noqa: F401  (puts implementation/obj-a on sys.path)

from lib.events import (
    arrive_via,
    depart_via,
    enters_server,
    error_no_out_edge,
    error_queue_full,
    exits_server,
    task_end,
    task_start,
)


class EventMessageTests(unittest.TestCase):
    def test_each_kind(self) -> None:
        self.assertEqual(
            arrive_via("j0", "q0", "inputs/in0.csv", now=10),
            "10.000 j0 IN q0 ARRIVE-VIA in0.csv",
        )
        self.assertEqual(
            depart_via("j0", "q0", "out/sink.csv", now=11),
            "11.000 j0 IN q0 DEPART-VIA sink.csv",
        )
        self.assertEqual(
            enters_server("j0", "q0", 1, now=12),
            "12.000 j0 IN q0 ENTERS-SERVER 1",
        )
        self.assertEqual(
            exits_server("j0", "q0", 1, now=13),
            "13.000 j0 IN q0 EXITS-SERVER 1",
        )
        self.assertEqual(
            task_start("j0", "q0", 0, 0.25, now=14),
            '14.000 j0 IN q0 TASK-START 0 "W 0.250000"',
        )
        self.assertEqual(
            task_end("j0", "q0", 0, 0.25, now=15),
            '15.000 j0 IN q0 TASK-END 0 "W 0.250000"',
        )
        self.assertEqual(
            error_queue_full("j1", "q0", now=16),
            "16.000 j1 IN q0 ERROR QUEUE-FULL",
        )
        self.assertEqual(
            error_no_out_edge("j1", "q0", now=17),
            "17.000 j1 IN q0 ERROR NO-OUT-EDGE",
        )


if __name__ == "__main__":
    unittest.main()
