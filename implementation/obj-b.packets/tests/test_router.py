"""The visits: event order, decisions, TTL, and what is forwarded."""

from __future__ import annotations

import io
import unittest

from support import capture_logger, event_kinds

from lib.job import Job, Task, format_job_line, ipv4_to_int, parse_job_line
from pk.router import Router, Sink
from pk.routing import QueueAddress, RoutingTable

ADDRESSES = {
    "q1": QueueAddress("q1", "127.0.0.1", 20001, 30001),
    "q7": QueueAddress("q7", "host7", 20007, 30007),
    "q8": QueueAddress("q8", "host8", 20008, 30008),
}
TABLE = RoutingTable([("10.0.0.0/24", "local"), ("10.0.1.0/24", "q1"), ("10.0.9.0/24", "q99")])
TO_LOCAL = "10.0.0.5"
TO_NEXT = "10.0.1.5"
UNROUTABLE = "192.168.0.1"
TASKS = (Task(0, 0.01), Task(1, 0.02), Task(2, 0.03))


def packet(dst: str = TO_NEXT, ttl: int = 64) -> Job:
    return parse_job_line(format_job_line("p0-0", 1.0, TASKS, dst_ip=ipv4_to_int(dst), ttl=ttl))


class Harness:
    """A router with a fake clock, a fake RPC, and a sink in memory."""

    def __init__(self, outcome: str = "accepted", **kwargs) -> None:
        self.log, self.messages = capture_logger(f"router-{id(self)}")
        self.slept: list[float] = []
        self.sent: list[tuple[str, Job, str]] = []
        self.sink_text = io.StringIO()

        def submit(dial, job, source):
            self.sent.append((dial.address, job, source))
            return outcome

        self.router = Router(
            "q5",
            0,
            self.log,
            addresses=ADDRESSES,
            table=TABLE,
            sink=Sink(self.sink_text, "sink-q5.csv"),
            sleep_fn=self.slept.append,
            submit_fn=submit,
            **kwargs,
        )

    @property
    def kinds(self) -> list[str]:
        return event_kinds(self.messages)


class MonolithTests(unittest.TestCase):
    def test_sleeps_all_three_tasks_in_order(self) -> None:
        harness = Harness()
        harness.router.monolith(packet())
        self.assertEqual(harness.slept, [0.01, 0.02, 0.03])

    def test_forward_reduces_ttl_and_keeps_the_row(self) -> None:
        harness = Harness()
        job = packet(ttl=64)
        harness.router.monolith(job)
        address, sent, source = harness.sent[0]
        self.assertEqual(address, "127.0.0.1:20001")
        self.assertEqual(sent.ttl, 63)
        self.assertEqual(sent.raw_line, job.raw_line)
        self.assertEqual(source, "q5")
        self.assertEqual(
            harness.kinds,
            ["ENTERS-SERVER", "TASK-START", "TASK-END", "TASK-START", "TASK-END", "TASK-START",
             "TASK-END", "EXITS-SERVER", "DEPART-VIA"],
        )

    def test_local_destination_goes_to_the_sink(self) -> None:
        harness = Harness()
        job = packet(TO_LOCAL)
        harness.router.monolith(job)
        self.assertEqual(harness.sink_text.getvalue(), job.raw_line + "\n")
        self.assertEqual(harness.sent, [])
        self.assertTrue(harness.messages[-1].endswith("DEPART-VIA sink-q5.csv"))

    def test_delivery_does_not_need_ttl_left_after_the_last_hop(self) -> None:
        harness = Harness()
        harness.router.monolith(packet(TO_LOCAL, ttl=1))
        self.assertEqual(harness.kinds[-1], "DEPART-VIA")

    def test_ttl_of_one_expires_rather_than_forwarding(self) -> None:
        harness = Harness()
        harness.router.monolith(packet(ttl=1))
        self.assertEqual(harness.kinds[-1], "ERROR TTL-EXPIRED")
        self.assertEqual(harness.sent, [])

    def test_ttl_of_two_forwards_with_one_left(self) -> None:
        harness = Harness()
        harness.router.monolith(packet(ttl=2))
        self.assertEqual(harness.sent[0][1].ttl, 1)

    def test_missing_ttl_is_expired_even_for_a_local_destination(self) -> None:
        harness = Harness()
        harness.router.monolith(packet(TO_LOCAL, ttl=0))
        self.assertEqual(harness.kinds[-1], "ERROR TTL-EXPIRED")
        self.assertEqual(harness.sink_text.getvalue(), "")

    def test_no_route_logs_no_out_edge(self) -> None:
        harness = Harness()
        harness.router.monolith(packet(UNROUTABLE))
        self.assertEqual(harness.kinds[-1], "ERROR NO-OUT-EDGE")
        self.assertEqual(harness.sent, [])

    def test_next_hop_without_an_address_is_not_silently_lost(self) -> None:
        harness = Harness()
        harness.router.monolith(packet("10.0.9.1"))
        self.assertEqual(harness.kinds[-1], "ERROR NO-OUT-EDGE")

    def test_rejected_hop_still_departs(self) -> None:
        harness = Harness(outcome="rejected")
        harness.router.monolith(packet())
        self.assertEqual(harness.kinds[-1], "DEPART-VIA")

    def test_transport_failure_does_not_depart(self) -> None:
        harness = Harness(outcome="failed")
        harness.router.monolith(packet())
        self.assertEqual(harness.kinds[-1], "EXITS-SERVER")


class StageTests(unittest.TestCase):
    def test_validate_sleeps_its_own_task_and_sends_to_the_next_stage(self) -> None:
        harness = Harness(next_qid="q7")
        job = packet()
        harness.router.validate(job)
        self.assertEqual(harness.slept, [0.01])
        address, sent, _ = harness.sent[0]
        self.assertEqual(address, "host7:20007")
        self.assertEqual(sent, job, "validate changes nothing")

    def test_validate_drops_a_missing_ttl_without_later_work(self) -> None:
        harness = Harness(next_qid="q7")
        harness.router.validate(packet(ttl=0))
        self.assertEqual(harness.kinds[-1], "ERROR TTL-EXPIRED")
        self.assertEqual(harness.sent, [])

    def test_lookup_sets_next_hop_and_keeps_the_ttl(self) -> None:
        harness = Harness(next_qid="q8")
        harness.router.lookup(packet(ttl=9))
        self.assertEqual(harness.slept, [0.02])
        address, sent, _ = harness.sent[0]
        self.assertEqual(address, "host8:20008")
        self.assertEqual(sent.next_hop, "q1")
        self.assertEqual(sent.ttl, 9)

    def test_lookup_drops_an_unroutable_packet(self) -> None:
        harness = Harness(next_qid="q8")
        harness.router.lookup(packet(UNROUTABLE))
        self.assertEqual(harness.kinds[-1], "ERROR NO-OUT-EDGE")
        self.assertEqual(harness.sent, [])

    def test_forward_uses_next_hop_reduces_ttl_and_clears_it(self) -> None:
        from dataclasses import replace

        harness = Harness()
        harness.router.forward(replace(packet(ttl=9), next_hop="q1"))
        self.assertEqual(harness.slept, [0.03])
        address, sent, _ = harness.sent[0]
        self.assertEqual(address, "127.0.0.1:20001")
        self.assertEqual(sent.ttl, 8)
        self.assertEqual(sent.next_hop, "")

    def test_forward_delivers_a_local_packet(self) -> None:
        from dataclasses import replace

        harness = Harness()
        job = replace(packet(TO_LOCAL), next_hop="local")
        harness.router.forward(job)
        self.assertEqual(harness.sink_text.getvalue(), job.raw_line + "\n")


class SameDecisionsTests(unittest.TestCase):
    """The monolith and the three stages must end a packet the same way."""

    def outcome_of_stages(self, job: Job) -> tuple:
        validate = Harness(next_qid="q7")
        validate.router.validate(job)
        if not validate.sent:
            return ("dropped", validate.kinds[-1])
        lookup = Harness(next_qid="q8")
        lookup.router.lookup(validate.sent[0][1])
        if not lookup.sent:
            return ("dropped", lookup.kinds[-1])
        forward = Harness()
        forward.router.forward(lookup.sent[0][1])
        return self.outcome(forward)

    def outcome_of_monolith(self, job: Job) -> tuple:
        monolith = Harness()
        monolith.router.monolith(job)
        return self.outcome(monolith)

    @staticmethod
    def outcome(harness: Harness) -> tuple:
        if harness.sent:
            address, sent, _ = harness.sent[0]
            return ("sent", address, sent.ttl, sent.next_hop)
        if harness.sink_text.getvalue():
            return ("delivered", harness.sink_text.getvalue())
        return ("dropped", harness.kinds[-1])

    def test_every_combination_ends_the_same_way(self) -> None:
        for dst in (TO_LOCAL, TO_NEXT, UNROUTABLE, "10.0.9.1"):
            for ttl in (0, 1, 2, 64):
                with self.subTest(dst=dst, ttl=ttl):
                    job = packet(dst, ttl)
                    self.assertEqual(self.outcome_of_monolith(job), self.outcome_of_stages(job))

    def test_all_stages_together_sleep_what_the_monolith_sleeps(self) -> None:
        job = packet()
        monolith = Harness()
        monolith.router.monolith(job)
        slept: list[float] = []
        for visit, next_qid in (("validate", "q7"), ("lookup", "q8")):
            harness = Harness(next_qid=next_qid)
            getattr(harness.router, visit)(job)
            slept += harness.slept
        from dataclasses import replace

        forward = Harness()
        forward.router.forward(replace(job, next_hop="q1"))
        self.assertEqual(slept + forward.slept, monolith.slept)


if __name__ == "__main__":
    unittest.main()
