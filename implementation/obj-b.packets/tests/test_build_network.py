"""Sampling, table generation, the path check, and the written files."""

from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path

from support import APP  # noqa: F401  (sets sys.path)

import build_network as build
from lib.job import ipv4_to_int
from pk.routing import RoutingTable, load_addresses, router_network

SPEC = json.loads((APP / "spec.json").read_text(encoding="utf-8"))


def spec(routers: int = 40, **changes) -> dict:
    return {**SPEC, "routers": routers, "sources": 5, **changes}


class SampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.graph = build.load_graph(APP / SPEC["topology"])

    def test_graph_matches_the_source_file(self) -> None:
        self.assertEqual(len(self.graph), 6474)
        self.assertEqual(sum(len(peers) for peers in self.graph.values()) // 2, 12572)

    def test_sample_is_connected_and_the_requested_size(self) -> None:
        nodes = build.random_walk_sample(self.graph, 200, 3)
        self.assertEqual(len(nodes), 200)
        links = build.induced_links(self.graph, nodes)
        self.assertTrue(all(hop is not None for hop in build.next_hops_towards(links, 0)[1:]))

    def test_same_seed_gives_the_same_sample(self) -> None:
        self.assertEqual(
            build.random_walk_sample(self.graph, 50, 7), build.random_walk_sample(self.graph, 50, 7)
        )

    def test_bad_size_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build.random_walk_sample(self.graph, 7000, 7)


class TableTests(unittest.TestCase):
    # 0 - 1 - 3 and 0 - 2 - 3: two equal paths from 0 to 3.
    LINKS = [[1, 2], [0, 3], [0, 3], [1, 2]]

    def test_ties_go_to_the_lowest_router(self) -> None:
        self.assertEqual(build.next_hops_towards(self.LINKS, 3)[0], 1)

    def test_destination_has_no_next_hop(self) -> None:
        self.assertIsNone(build.next_hops_towards(self.LINKS, 3)[3])

    def test_check_paths_reports_mean_and_longest(self) -> None:
        stats = build.check_paths(build.build_tables(self.LINKS), 64)
        self.assertEqual(stats["longest_path_hops"], 2)
        self.assertAlmostEqual(stats["mean_hops"], 4 / 3)

    def test_check_paths_fails_on_a_loop(self) -> None:
        tables = build.build_tables(self.LINKS)
        tables[3][0], tables[3][1] = 1, 0
        with self.assertRaisesRegex(SystemExit, "loop-free"):
            build.check_paths(tables, 64)

    def test_check_paths_fails_on_a_dead_end(self) -> None:
        with self.assertRaisesRegex(SystemExit, "loop-free"):
            build.check_paths(build.build_tables([[1], [0], [3], [2]]), 64)

    def test_check_paths_fails_when_a_path_needs_more_than_the_ttl(self) -> None:
        with self.assertRaisesRegex(SystemExit, "TTL"):
            build.check_paths(build.build_tables(self.LINKS), 2)


class SourceTests(unittest.TestCase):
    def test_one_link_routers_are_chosen_first(self) -> None:
        links = [[1], [0, 2, 3], [1], [1]]
        self.assertEqual(build.choose_sources(links, 3, 1), [0, 2, 3])

    def test_tops_up_when_there_are_too_few_one_link_routers(self) -> None:
        links = [[1], [0, 2, 3], [1, 3], [1, 2]]
        sources = build.choose_sources(links, 2, 1)
        self.assertEqual(len(sources), 2)
        self.assertIn(0, sources)


class VisitRateTests(unittest.TestCase):
    # A line: 0 - 1 - 2. Traffic from 0 goes half to 1 and half to 2.
    LINE = [[1], [0, 2], [1]]

    def test_visits_count_every_router_on_each_path(self) -> None:
        visits = build.visit_rates(build.build_tables(self.LINE), [0])
        self.assertEqual(visits, [1.0, 1.0, 0.5])

    def test_total_visits_are_mean_routers_on_a_path(self) -> None:
        visits = build.visit_rates(build.build_tables(self.LINE), [0, 1, 2])
        tables = build.build_tables(self.LINE)
        self.assertAlmostEqual(sum(visits), build.check_paths(tables, 64)["mean_hops"] + 1)


class AllocationTests(unittest.TestCase):
    def test_total_is_exact_and_follows_weights(self) -> None:
        self.assertEqual(build.allocate_servers([3.0, 1.0], 8), [6, 2])

    def test_every_router_keeps_at_least_one(self) -> None:
        servers = build.allocate_servers([100.0, 0.001, 0.001], 5)
        self.assertEqual(servers, [3, 1, 1])

    def test_totals_hold_for_many_budgets(self) -> None:
        weights = [0.1, 0.9, 0.4, 0.2, 0.2, 0.7, 0.3]
        for total in range(7, 60):
            self.assertEqual(sum(build.allocate_servers(weights, total)), total)

    def test_too_few_servers_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build.allocate_servers([1.0, 1.0], 1)

    def test_buffers_can_stay_fixed_while_servers_change(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            low = build.build(spec(routers=10, total_servers=14, buffer_servers=20), build.MONOLITH, Path(first), ["h"])
            high = build.build(spec(routers=10, total_servers=40, buffer_servers=20), build.MONOLITH, Path(second), ["h"])
        self.assertEqual([r["qsize"] for r in low["routers"]], [r["qsize"] for r in high["routers"]])
        self.assertEqual(sum(r["servers"] for r in low["routers"]), 14)
        self.assertEqual(sum(r["servers"] for r in high["routers"]), 40)


class WrittenFilesTests(unittest.TestCase):
    def walk(self, out: Path, topology: dict, mode: str) -> None:
        """Follow the written tables for every pair, the way a router would."""
        lookup_stage = 1 if mode == build.STAGES else 0
        routers = topology["routers"]
        tables = {
            build.queue_id(entry["router"], mode, 0): RoutingTable.load(
                out / "routes" / f"{build.queue_id(entry['router'], mode, lookup_stage)}.tsv"
            )
            for entry in routers
        }
        for source in routers:
            for destination in routers:
                address = router_network(destination["router"]) | 7
                current = build.queue_id(source["router"], mode, 0)
                expected_end = build.queue_id(destination["router"], mode, 0)
                for _ in range(len(routers)):
                    following = tables[current].lookup(address)
                    if following == "local":
                        break
                    current = following
                else:
                    self.fail(f"no arrival from {source['router']} to {destination['router']}")
                self.assertEqual(current, expected_end)

    def test_monolith_tables_deliver_every_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            topology = build.build(spec(), build.MONOLITH, out, ["127.0.0.1"])
            self.walk(out, topology, build.MONOLITH)
            self.assertEqual(len(load_addresses(out / "addresses.csv")), 40)

    def test_stage_tables_deliver_every_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            topology = build.build(spec(), build.STAGES, out, ["127.0.0.1"])
            self.walk(out, topology, build.STAGES)
            self.assertEqual(len(load_addresses(out / "addresses.csv")), 120)

    def test_stage_queues_are_three_per_router_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            topology = build.build(spec(), build.STAGES, Path(directory), ["127.0.0.1"])
        self.assertEqual(
            topology["routers"][2]["queues"], {"validate": "q6", "lookup": "q7", "forward": "q8"}
        )

    def test_queue_sizes_and_servers_follow_the_spec(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            topology = build.build(
                spec(degree_per_server=2, qsize_per_server=10, min_qsize=1), build.MONOLITH, out, ["h"]
            )
            rows = (out / "queues.csv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(rows[0], "qid,qsize,servers")
        for entry in topology["routers"]:
            self.assertEqual(entry["servers"], max(1, -(-len(entry["links"]) // 2)))
            self.assertEqual(entry["qsize"], 10 * entry["servers"])

    def test_same_spec_gives_identical_tables(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            build.build(spec(), build.MONOLITH, Path(first), ["h"])
            build.build(spec(), build.MONOLITH, Path(second), ["h"])
            for path in sorted(Path(first, "routes").glob("*.tsv")):
                self.assertEqual(path.read_bytes(), Path(second, "routes", path.name).read_bytes())

    def test_uses_addresses_within_a_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            build.build(spec(routers=10), build.MONOLITH, out, ["h"])
            table = RoutingTable.load(out / "routes" / "q4.tsv")
        self.assertEqual(table.lookup(ipv4_to_int("10.0.4.200")), "local")
        self.assertIsNone(table.lookup(ipv4_to_int("10.0.10.1")))


class RandomGraphTests(unittest.TestCase):
    def test_random_connected_graphs_always_pass_the_check(self) -> None:
        rng = random.Random(5)
        for _ in range(20):
            size = rng.randint(2, 40)
            links = [set() for _ in range(size)]
            for node in range(1, size):
                peer = rng.randrange(node)
                links[node].add(peer)
                links[peer].add(node)
            stats = build.check_paths(build.build_tables([sorted(p) for p in links]), 64)
            self.assertGreaterEqual(stats["mean_hops"], 1)


if __name__ == "__main__":
    unittest.main()
