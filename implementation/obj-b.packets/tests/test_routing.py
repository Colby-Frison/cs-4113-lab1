"""Addresses, longest-prefix match, and the address book."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from support import APP  # noqa: F401  (sets sys.path)

from lib.job import ipv4_to_int
from pk.routing import (
    RoutingTable,
    load_addresses,
    loopback_hosts,
    router_network,
    router_prefix,
    write_addresses,
)


class PrefixTests(unittest.TestCase):
    def test_router_prefix_uses_both_octets(self) -> None:
        self.assertEqual(router_prefix(17), "10.0.17.0/24")
        self.assertEqual(router_prefix(912), "10.3.144.0/24")

    def test_network_matches_prefix(self) -> None:
        self.assertEqual(router_network(912), ipv4_to_int("10.3.144.0"))

    def test_router_index_out_of_range_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            router_prefix(65536)


class LookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.table = RoutingTable(
            [
                ("10.0.0.0/8", "q1"),
                ("10.3.0.0/16", "q2"),
                ("10.3.144.0/24", "q3"),
                ("10.0.17.0/24", "local"),
            ]
        )

    def test_longest_prefix_wins(self) -> None:
        self.assertEqual(self.table.lookup(ipv4_to_int("10.3.144.7")), "q3")
        self.assertEqual(self.table.lookup(ipv4_to_int("10.3.200.7")), "q2")
        self.assertEqual(self.table.lookup(ipv4_to_int("10.9.9.9")), "q1")

    def test_local_route(self) -> None:
        self.assertEqual(self.table.lookup(ipv4_to_int("10.0.17.1")), "local")

    def test_no_route(self) -> None:
        self.assertIsNone(self.table.lookup(ipv4_to_int("192.168.0.1")))

    def test_default_route(self) -> None:
        table = RoutingTable([("0.0.0.0/0", "q9")])
        self.assertEqual(table.lookup(ipv4_to_int("1.2.3.4")), "q9")

    def test_bad_prefix_is_rejected(self) -> None:
        for prefix in ("10.0.0.256/24", "10.0.0/24", "10.0.0.0/33", "10.0.0.0"):
            with self.subTest(prefix), self.assertRaises(ValueError):
                RoutingTable([(prefix, "q1")])


class FileTests(unittest.TestCase):
    def test_table_file_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "table.tsv"
            path.write_text("# comment\n10.0.1.0/24\tq1\n\n10.0.2.0/24\tlocal\n", encoding="utf-8")
            table = RoutingTable.load(path)
        self.assertEqual(table.lookup(ipv4_to_int("10.0.1.9")), "q1")
        self.assertEqual(table.lookup(ipv4_to_int("10.0.2.9")), "local")

    def test_malformed_table_line_names_the_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "table.tsv"
            path.write_text("10.0.1.0/24 q1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, ":1:"):
                RoutingTable.load(path)

    def test_addresses_spread_evenly_and_ports_do_not_collide(self) -> None:
        qids = [f"q{i}" for i in range(3000)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "addresses.csv"
            write_addresses(qids, ["a", "b", "c"], path)
            addresses = load_addresses(path)
        hosts = [address.host for address in addresses.values()]
        self.assertEqual(hosts.count("a"), 1000)
        self.assertEqual(hosts.count("c"), 1000)
        ports = [(a.host, p) for a in addresses.values() for p in (a.queue_port, a.server_port)]
        self.assertEqual(len(ports), len(set(ports)))
        self.assertLess(max(port for _, port in ports), 32768, "stay below the usual ephemeral range")

    def test_a_large_network_on_one_machine_uses_more_loopback_addresses(self) -> None:
        qids = [f"q{i}" for i in range(10200)]
        hosts = loopback_hosts(len(qids))
        self.assertEqual(hosts, ["127.0.0.1", "127.0.0.2"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "addresses.csv"
            write_addresses(qids, hosts, path)
            addresses = load_addresses(path)
        ports = [(a.host, p) for a in addresses.values() for p in (a.queue_port, a.server_port)]
        self.assertEqual(len(ports), len(set(ports)))
        self.assertLess(max(port for _, port in ports), 32768)

    def test_too_many_queues_for_one_host_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "more than"):
                write_addresses([f"q{i}" for i in range(6001)], ["a"], Path(directory) / "a.csv")


if __name__ == "__main__":
    unittest.main()
