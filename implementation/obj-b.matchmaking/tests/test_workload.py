"""Generator mixes, scale, and the count/duration contract."""

from __future__ import annotations

import io
import random
import unittest

import support

from lib.job import iter_jobs
from mm.model import SEARCH_MEAN, search_mean
from mm.ticket import parse_ticket
from mm.workload import WorkloadConfig, generate

del support


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)

    def time(self) -> float:
        return 1_700_000_000.0 + self.now


class WorkloadTests(unittest.TestCase):
    def test_count_writes_six_tasks_and_repeats_under_a_seed(self) -> None:
        clock = Clock()
        config = WorkloadConfig(
            count=3,
            seed=4,
            time_scale=1.0,
            arrival_mean=0.0,
            region="na",
            mode="ranked",
        )
        first = io.StringIO()
        second = io.StringIO()
        self.assertEqual(generate(config, first, now_fn=clock.time, sleep_fn=clock.sleep), 3)
        self.assertEqual(
            generate(config, second, now_fn=Clock().time, sleep_fn=lambda _seconds: None),
            3,
        )
        self.assertEqual(first.getvalue(), second.getvalue())
        jobs = list(iter_jobs(first.getvalue().splitlines()))
        self.assertEqual(len(jobs), 3)
        self.assertEqual([task.tid for task in jobs[0].tasks], [0, 1, 2, 3, 4, 5])
        ticket = parse_ticket(jobs[0].job_id)
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual(ticket.region, "na")
        self.assertEqual(ticket.mode, "ranked")
        self.assertEqual(jobs[0].tasks[2].wait_seconds, 0.02)
        self.assertEqual(jobs[0].tasks[4].wait_seconds, 2.0)

    def test_time_scale_zero_writes_zero_waits(self) -> None:
        config = WorkloadConfig(count=1, seed=1, time_scale=0.0, arrival_mean=0.0, region="eu", mode="unrated")
        buffer = io.StringIO()
        generate(config, buffer, sleep_fn=lambda _seconds: None, now_fn=lambda: 10.0)
        job = next(iter_jobs(buffer.getvalue().splitlines()))
        self.assertTrue(all(task.wait_seconds == 0.0 for task in job.tasks))
        ticket = parse_ticket(job.job_id)
        self.assertIsNotNone(ticket)
        assert ticket is not None
        self.assertEqual((ticket.region, ticket.mode, ticket.seq), ("eu", "unrated", 0))

    def test_duration_stops_during_the_arrival_sleep(self) -> None:
        clock = Clock()

        class Half(random.Random):
            def random(self) -> float:  # noqa: D102
                return 0.5

        config = WorkloadConfig(duration=1.0, arrival_mean=10.0, time_scale=1.0, region="na", mode="ranked")
        buffer = io.StringIO()
        emitted = generate(
            config,
            buffer,
            rng=Half(),
            sleep_fn=clock.sleep,
            monotonic_fn=clock.monotonic,
            now_fn=clock.time,
        )
        self.assertEqual(emitted, 0)
        self.assertIn("Job Id,", buffer.getvalue())

    def test_search_mean_grows_for_apex_and_large_parties(self) -> None:
        self.assertGreater(search_mean("apex", 1), search_mean("high", 1))
        self.assertGreater(search_mean("mid", 5), search_mean("mid", 1))
        self.assertEqual(search_mean("low", 2), SEARCH_MEAN["low"])

    def test_party_mix_is_skewed_toward_solos(self) -> None:
        config = WorkloadConfig(count=4000, seed=3, time_scale=0.0, arrival_mean=0.0)
        buffer = io.StringIO()
        generate(config, buffer, sleep_fn=lambda _seconds: None, now_fn=lambda: 1.0)
        parties = []
        for job in iter_jobs(buffer.getvalue().splitlines()):
            ticket = parse_ticket(job.job_id)
            self.assertIsNotNone(ticket)
            assert ticket is not None
            parties.append(ticket.party)
        solos = sum(1 for party in parties if party == 1)
        self.assertGreater(solos / len(parties), 0.45)
        self.assertLess(solos / len(parties), 0.65)


if __name__ == "__main__":
    unittest.main()
