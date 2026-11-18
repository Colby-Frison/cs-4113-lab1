"""The packet stream: row format and the chosen distributions."""

from __future__ import annotations

import io
import math
import random
import statistics
import unittest

from support import APP, load_script

from lib.job import is_header, parse_job_line
from pk.routing import router_network

GENERATOR = load_script(APP / "workload-generator" / "workload_generator.py", "pk_generator")


def run(*extra: str, rate: str = "10"):
    """Generate without real sleeps. Returns the packets, sleeps, and raw text."""
    args = GENERATOR.build_parser().parse_args(
        ["--source", "3", "--routers", "10", "--rate", rate, *extra]
    )
    sleeps: list[float] = []
    output = io.StringIO()
    clock_now = [1000.0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock_now[0] += seconds

    GENERATOR.generate([output], args, sleep=sleep, clock=lambda: clock_now[0])
    lines = output.getvalue().splitlines()
    return [parse_job_line(line) for line in lines[1:]], sleeps, lines


class RowTests(unittest.TestCase):
    def test_header_then_one_row_per_packet(self) -> None:
        packets, _, lines = run("--count", "5")
        self.assertTrue(is_header(lines[0]))
        self.assertEqual(len(packets), 5)

    def test_ids_name_the_source_and_are_unique(self) -> None:
        packets, _, _ = run("--count", "50")
        self.assertEqual(packets[0].job_id, "p3-0")
        self.assertEqual(len({packet.job_id for packet in packets}), 50)

    def test_rows_carry_the_ttl_and_three_tasks(self) -> None:
        packets, _, _ = run("--count", "5", "--ttl", "9")
        for packet in packets:
            self.assertEqual(packet.ttl, 9)
            self.assertEqual([task.tid for task in packet.tasks], [0, 1, 2])

    def test_copy_output_receives_the_same_bytes(self) -> None:
        args = GENERATOR.build_parser().parse_args(
            ["--source", "0", "--routers", "4", "--rate", "100", "--count", "3"]
        )
        first, second = io.StringIO(), io.StringIO()
        GENERATOR.generate([first, second], args, sleep=lambda _: None)
        self.assertEqual(first.getvalue(), second.getvalue())


class DistributionTests(unittest.TestCase):
    def test_gaps_have_the_exponential_mean_and_spread(self) -> None:
        _, sleeps, _ = run("--count", "5000", rate="10")
        self.assertAlmostEqual(statistics.mean(sleeps), 0.1, delta=0.01)
        # An exponential's standard deviation equals its mean.
        self.assertAlmostEqual(statistics.stdev(sleeps), 0.1, delta=0.01)

    def test_destination_is_never_the_source_and_covers_every_other_router(self) -> None:
        packets, _, _ = run("--count", "2000")
        routers = {(packet.dst_ip >> 8) & 0xFFFF for packet in packets}
        self.assertEqual(routers, set(range(10)) - {3})

    def test_destination_host_stays_inside_the_prefix(self) -> None:
        rng = random.Random(1)
        for _ in range(1000):
            address = GENERATOR.draw_destination(rng, 0, 2)
            self.assertEqual(address & 0xFFFFFF00, router_network(1))
            self.assertTrue(1 <= address & 0xFF <= 254)

    def test_forward_wait_averages_to_the_requested_mean(self) -> None:
        packets, _, _ = run("--count", "5000", "--forward-wait-mean", "0.03")
        waits = [packet.tasks[2].wait_seconds for packet in packets]
        self.assertAlmostEqual(statistics.mean(waits), 0.03, delta=0.002)
        self.assertEqual(len({round(wait, 5) for wait in waits}), 3, "one wait per IMIX size")

    def test_validate_and_lookup_waits_are_fixed(self) -> None:
        packets, _, _ = run("--count", "20", "--validate-wait", "0.005", "--lookup-wait", "0.015")
        self.assertEqual({packet.tasks[0].wait_seconds for packet in packets}, {0.005})
        self.assertEqual({packet.tasks[1].wait_seconds for packet in packets}, {0.015})


class ReproducibilityTests(unittest.TestCase):
    def test_same_seed_gives_the_same_packets(self) -> None:
        first, _, _ = run("--count", "30", "--seed", "4")
        second, _, _ = run("--count", "30", "--seed", "4")
        self.assertEqual(
            [(p.dst_ip, p.tasks) for p in first], [(p.dst_ip, p.tasks) for p in second]
        )

    def test_changing_the_rate_keeps_the_same_packets(self) -> None:
        slow, _, _ = run("--count", "30", rate="5")
        fast, _, _ = run("--count", "30", rate="500")
        self.assertEqual([(p.dst_ip, p.tasks) for p in slow], [(p.dst_ip, p.tasks) for p in fast])


class LimitTests(unittest.TestCase):
    def test_duration_stops_before_a_partial_gap(self) -> None:
        packets, sleeps, _ = run("--duration", "10", rate="10")
        self.assertLess(sum(sleeps), 10)
        self.assertTrue(math.isclose(len(packets), 100, abs_tol=40))


if __name__ == "__main__":
    unittest.main()
