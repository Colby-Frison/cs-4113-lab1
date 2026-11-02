"""Pattern expansion, server totals, and the bracket shard formula."""

from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path

import support

from lib.job import Job, Task
from mm.stages import RETRY_SHARD, BracketTable, make_edge_chooser, search_qid_for_ticket
from mm.ticket import Ticket
from studies.expand import backend_for, build_layout, load_pattern, search_worker_total
from studies.plots import write_sensitivity
from studies.scale import SCENARIOS, scale_layout

del support

HERE = Path(__file__).resolve().parents[1] / "studies"
COUNTS = {"medium": 45, "large": 205, "very-large": 1005}
BANDS_PER_SIZE = {"medium": (10, 100), "large": (100, 1000), "very-large": (1000, 10000)}


class ExpandTests(unittest.TestCase):
    def test_each_size_lands_in_its_band(self) -> None:
        for size, count in COUNTS.items():
            layout = build_layout(load_pattern(HERE / size / "pattern.json"))
            low, high = BANDS_PER_SIZE[size]
            self.assertEqual(len(layout.queues), count)
            self.assertGreaterEqual(count, low)
            self.assertLessEqual(count, high)
            self.assertEqual(layout.queues[0].stage, "admit")
            self.assertEqual(layout.queues[1].stage, "rating")
            self.assertEqual(layout.queues[2].stage, "route")
            self.assertEqual(layout.queues[-2].stage, "ready")
            self.assertEqual(layout.queues[-1].stage, "allocate")
            numbers = [queue.number for queue in layout.search_queues]
            self.assertEqual(numbers, list(range(numbers[0], numbers[0] + len(numbers))))
            self.assertTrue(all(queue.qid[1:].isdigit() for queue in layout.queues))
            expected = "process" if count <= 80 else "hosted"
            self.assertEqual(backend_for(layout), expected)

    def test_small_is_one_search_queue(self) -> None:
        layout = build_layout(load_pattern(HERE / "small" / "pattern.json"))
        self.assertEqual(len(layout.queues), 6)
        self.assertGreaterEqual(6, 2)
        self.assertLessEqual(6, 10)
        self.assertTrue(layout.single_search)
        self.assertEqual(backend_for(layout), "process")
        self.assertEqual(
            [queue.stage for queue in layout.queues],
            ["admit", "rating", "route", "search", "ready", "allocate"],
        )
        self.assertEqual(layout.search_queues[0].qid, "q3")
        self.assertEqual(layout.search_queues[0].workers, 10)
        self.assertEqual(next(queue.workers for queue in layout.queues if queue.stage == "allocate"), 10)
        self.assertEqual(next(queue.workers for queue in layout.queues if queue.stage == "ready"), 1)
        for band in ("placement", "low", "mid", "high", "apex"):
            ticket = Ticket(3, "na", "ranked", band, 1)
            self.assertEqual(layout.search_qid(ticket), "q3")
        self.assertEqual(layout.search_qid(Ticket(8, "na", "ranked", "apex", 2)), "q3")
        table = BracketTable(
            replicas=1,
            search_base=3,
            destinations={"q3": "127.0.0.1:9"},
            regions=layout.regions,
            modes=layout.modes,
            bands=layout.bands,
            single=True,
        )
        placement = Job("t3.na.ranked.placement.p1", 0.0, (Task(0, 0.0),), "raw")
        apex = Job("t3.na.ranked.apex.p1", 0.0, (Task(0, 0.0),), "raw")
        route = make_edge_chooser("route", bracket=table)
        self.assertEqual(route(placement, [], [], random.Random(0)), "127.0.0.1:9")
        self.assertEqual(route(apex, [], [], random.Random(0)), "127.0.0.1:9")
        totals = [
            search_worker_total(scale_layout(layout, factor))
            for _name, factor in SCENARIOS
        ]
        self.assertEqual(totals, [10, 9, 8, 7, 12, 15, 20])
        shrunk = scale_layout(layout, 0.7)
        self.assertEqual(next(queue.workers for queue in shrunk.queues if queue.stage == "allocate"), 7)
        self.assertEqual(next(queue.workers for queue in shrunk.queues if queue.stage == "ready"), 1)
        medium = build_layout(load_pattern(HERE / "medium" / "pattern.json"))
        medium_shrunk = scale_layout(medium, 0.7)
        self.assertTrue(all(queue.workers == 1 for queue in medium_shrunk.queues if queue.stage != "search"))

    def test_seven_search_totals_differ(self) -> None:
        for size in COUNTS:
            layout = build_layout(load_pattern(HERE / size / "pattern.json"))
            totals = [
                search_worker_total(scale_layout(layout, factor))
                for _name, factor in SCENARIOS
            ]
            self.assertEqual(len(totals), 7)
            self.assertEqual(len(set(totals)), 7, totals)

    def test_retry_uses_the_same_shard(self) -> None:
        layout = build_layout(load_pattern(HERE / "large" / "pattern.json"))
        first = Ticket(3, "na", "ranked", "placement", 1)
        again = Ticket(8, "na", "ranked", "placement", 1)
        other = Ticket(4, "na", "ranked", "placement", 1)
        self.assertEqual(layout.search_qid(first), layout.search_qid(again))
        self.assertNotEqual(layout.search_qid(first), layout.search_qid(other))

        qid = layout.search_qid(first)
        table = BracketTable(
            replicas=layout.replicas,
            search_base=layout.search_base,
            destinations={qid: "127.0.0.1:9"},
            regions=layout.regions,
            modes=layout.modes,
            bands=layout.bands,
        )
        job = Job(first.job_id, 0.0, (Task(0, 0.0),), "raw")
        route = make_edge_chooser("route", bracket=table)
        self.assertEqual(route(job, [], [], random.Random(0)), "127.0.0.1:9")
        state = {first.job_id: 1}
        ready = make_edge_chooser(
            "ready",
            ready=("pass-edge", RETRY_SHARD, "fail-edge"),
            state=state,
            fail_p=1.0,
            max_visits=2,
            bracket=table,
        )
        self.assertEqual(ready(job, [], [], random.Random(0)), "127.0.0.1:9")
        state[first.job_id] = 2
        self.assertEqual(ready(job, [], [], random.Random(0)), "fail-edge")
        self.assertEqual(
            search_qid_for_ticket(first, replicas=5, search_base=3),
            search_qid_for_ticket(again, replicas=5, search_base=3),
        )

    def test_process_commands_keep_the_region_map_off_the_command_line(self) -> None:
        from studies.run_study import _assign_ports, _queue_args, _server_args

        layout = build_layout(load_pattern(HERE / "medium" / "pattern.json"))
        ports = _assign_ports(layout, 29100)
        run = Path("runs")
        route = next(queue for queue in layout.queues if queue.server == "route")
        ready = next(queue for queue in layout.queues if queue.server == "ready")
        route_args = _server_args(route, layout, ports, run)
        ready_args = _server_args(ready, layout, ports, run)
        self.assertIn("--bracket-layout", route_args)
        self.assertNotIn("--route-edge", route_args)
        self.assertIn("--fail", ready_args)
        self.assertNotIn("--retry", ready_args)
        admit_args = _queue_args(layout.queues[0], ports, run, "/tmp/jobs.fifo")
        self.assertIn("/tmp/jobs.fifo", admit_args)
        self.assertNotIn("--ingrpc", admit_args)

    def test_region_map_still_wins_without_a_bracket(self) -> None:
        job = Job("t0.na.ranked.mid.p1", 0.0, (Task(0, 0.0),), "raw")
        choose = make_edge_chooser("route", routes={"na": "search-na.csv"})
        self.assertEqual(choose(job, [], [], random.Random(0)), "search-na.csv")

    def test_sensitivity_figure(self) -> None:
        rows = [
            {
                "name": "base",
                "workers": 160,
                "count": 10,
                "queue_full": 0,
                "failed": 1,
                "mean_latency": 10.0,
                "throughput": 1.0,
            },
            {
                "name": "srv-30",
                "workers": 112,
                "count": 10,
                "queue_full": 0,
                "failed": 2,
                "mean_latency": 12.0,
                "throughput": 0.8,
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_sensitivity(rows, root, "medium")
            self.assertTrue((root / "sensitivity-servers.png").is_file())
            self.assertTrue((root / "sensitivity-latency.png").is_file())


if __name__ == "__main__":
    unittest.main()
