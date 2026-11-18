"""Packet rows, packet header fields on gRPC, and drops in the run analyzer."""

from __future__ import annotations

import unittest

from support import ROOT, load_script

from lib.job import (
    HEADER,
    PACKET_HEADER,
    JobParseError,
    Task,
    format_job_line,
    int_to_ipv4,
    ipv4_to_int,
    is_header,
    parse_job_line,
)
from lib.events import error_ttl_expired

ANALYZE = load_script(ROOT / "mockups" / "analyze_run.py", "obj_a_analyze_run")
TASKS = (Task(0, 0.25),)

try:
    import grpc  # noqa: F401

    from lib.grpc_runtime import _request, job_from_message

    HAVE_GRPC = True
except ImportError:
    HAVE_GRPC = False


class RowTests(unittest.TestCase):
    def test_packet_row_round_trip(self) -> None:
        line = format_job_line("p1-0", 1.0, TASKS, dst_ip=ipv4_to_int("10.3.144.7"), ttl=64)
        job = parse_job_line(line)
        self.assertEqual(job.dst_ip, ipv4_to_int("10.3.144.7"))
        self.assertEqual(job.ttl, 64)
        self.assertTrue(line.endswith(",10.3.144.7,64"))
        self.assertEqual(job.raw_line, line)

    def test_three_column_rows_are_unchanged(self) -> None:
        line = format_job_line("j0", 1.0, TASKS)
        self.assertEqual(line.count(","), 2)
        job = parse_job_line(line)
        self.assertEqual((job.dst_ip, job.ttl, job.next_hop), (0, 0, ""))

    def test_both_headers_are_headers(self) -> None:
        self.assertTrue(is_header(HEADER))
        self.assertTrue(is_header(PACKET_HEADER))
        with self.assertRaises(JobParseError):
            parse_job_line(PACKET_HEADER)

    def test_four_columns_are_rejected(self) -> None:
        line = format_job_line("j0", 1.0, TASKS) + ",10.0.0.1"
        with self.assertRaisesRegex(JobParseError, "3 or 5"):
            parse_job_line(line)

    def test_bad_packet_columns_are_rejected(self) -> None:
        base = format_job_line("j0", 1.0, TASKS)
        for suffix in (",10.0.0.256,64", ",10.0.0.1,many", ",10.0.0.1,-1"):
            with self.subTest(suffix), self.assertRaises(JobParseError):
                parse_job_line(base + suffix)

    def test_dst_and_ttl_must_come_together(self) -> None:
        with self.assertRaises(ValueError):
            format_job_line("j0", 1.0, TASKS, dst_ip=1)

    def test_address_conversion_round_trip(self) -> None:
        self.assertEqual(int_to_ipv4(ipv4_to_int("255.0.1.2")), "255.0.1.2")


class EventTests(unittest.TestCase):
    def test_ttl_expired_line(self) -> None:
        self.assertEqual(error_ttl_expired("p0-1", "q2", now=10), "10.000 p0-1 IN q2 ERROR TTL-EXPIRED")


@unittest.skipUnless(HAVE_GRPC, "grpcio is not installed")
class MessageTests(unittest.TestCase):
    def test_ttl_in_the_message_beats_the_row(self) -> None:
        job = parse_job_line(format_job_line("p0-0", 1.0, TASKS, dst_ip=ipv4_to_int("10.0.0.1"), ttl=64))
        message = _request(job, "q0").job
        message.ttl = 12
        message.next_hop = "q3"
        received = job_from_message(message)
        self.assertEqual(received.ttl, 12, "the row still says 64")
        self.assertEqual(received.next_hop, "q3")
        self.assertEqual(received.dst_ip, ipv4_to_int("10.0.0.1"))
        self.assertEqual(received.raw_line, job.raw_line)

    def test_jobs_without_packet_fields_are_unchanged(self) -> None:
        job = parse_job_line(format_job_line("j0", 1.0, TASKS))
        self.assertEqual(job_from_message(_request(job, "q0").job), job)


class AnalyzerDropTests(unittest.TestCase):
    """How each ending shows up to the end-to-end metrics."""

    @staticmethod
    def record(when: float, qid: str, kind: str, rest: str = "", component: str = "sv") -> object:
        return ANALYZE.Record(when, "p0-0", qid, kind, rest, component, int(when * 1000))

    def departures(self, records: list) -> list[str]:
        return [r.rest for r in ANALYZE.prepare(records) if r.kind == "DEPART-VIA"]

    def first_hop(self) -> list:
        return [
            self.record(1.0, "q0", "ARRIVE-VIA", "jobs.csv", "Q"),
            self.record(1.1, "q0", "DEPART-VIA", "host:20000", "Q"),
            self.record(1.2, "q0", "ENTERS-SERVER", "0"),
            self.record(1.3, "q0", "EXITS-SERVER", "0"),
        ]

    def test_delivered_packet_exits_at_its_sink(self) -> None:
        records = self.first_hop() + [
            self.record(1.4, "q1", "ARRIVE-VIA", "q0", "Q"),
            self.record(1.5, "q0", "DEPART-VIA", "host:20001"),
            self.record(1.6, "q1", "DEPART-VIA", "host:20002", "Q"),
            self.record(1.7, "q1", "ENTERS-SERVER", "0"),
            self.record(1.8, "q1", "EXITS-SERVER", "0"),
            self.record(1.9, "q1", "DEPART-VIA", "sink-q1.csv"),
        ]
        self.assertEqual(self.departures(records), ["q1", "sink-q1.csv"])

    def test_packet_rejected_by_the_next_queue_does_not_exit(self) -> None:
        records = self.first_hop() + [
            self.record(1.4, "q1", "ERROR", "QUEUE-FULL", "Q"),
            self.record(1.5, "q0", "DEPART-VIA", "host:20001"),
        ]
        self.assertEqual(self.departures(records), ["q0"], "a queue id is not a system exit")

    def test_packet_whose_ttl_expired_does_not_exit(self) -> None:
        records = self.first_hop() + [
            self.record(1.4, "q1", "ARRIVE-VIA", "q0", "Q"),
            self.record(1.5, "q0", "DEPART-VIA", "host:20001"),
            self.record(1.6, "q1", "DEPART-VIA", "host:20002", "Q"),
            self.record(1.7, "q1", "ENTERS-SERVER", "0"),
            self.record(1.8, "q1", "EXITS-SERVER", "0"),
            self.record(1.9, "q1", "ERROR", "TTL-EXPIRED"),
        ]
        self.assertEqual(self.departures(records), ["q1"])


if __name__ == "__main__":
    unittest.main()
