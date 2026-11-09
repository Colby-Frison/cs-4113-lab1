"""Stable shard routing for the KV store."""

from __future__ import annotations

import random
import unittest

import support  # noqa: F401  — puts implementation/obj-a on sys.path

from lib.grpc_spec import parse_dial
from lib.job import Task, format_job_line, parse_job_line
from lib.kv_routing import make_shard_edge_chooser, partition_lines, shard_index


class KvRoutingTests(unittest.TestCase):
    def test_same_key_same_shard(self) -> None:
        for n in (2, 3, 4, 8):
            index = shard_index("j42", n)
            self.assertEqual(shard_index("j42", n), index)
            self.assertGreaterEqual(index, 0)
            self.assertLess(index, n)

    def test_keys_spread_across_shards(self) -> None:
        for n in (2, 3, 4):
            seen = {shard_index(f"job-{i}", n) for i in range(200)}
            self.assertEqual(seen, set(range(n)))

    def test_partition_lines_by_job_id(self) -> None:
        lines = [
            format_job_line("j0", 1.0, (Task(0, 0.1),)),
            format_job_line("j1", 1.0, (Task(0, 0.1),)),
            format_job_line("j2", 1.0, (Task(0, 0.1),)),
        ]
        buckets = partition_lines(lines, 2, key_from_line=lambda line: parse_job_line(line).job_id)
        self.assertEqual(sum(len(bucket) for bucket in buckets), 3)
        for line in lines:
            job = parse_job_line(line)
            self.assertIn(line, buckets[shard_index(job.job_id, 2)])

    def test_edge_chooser_picks_shard_outfile(self) -> None:
        choose = make_shard_edge_chooser(2)
        job = parse_job_line(format_job_line("j0", 1.0, (Task(0, 0.0),)))
        dest = choose(job, ["a.csv", "b.csv"], [], random.Random(0))
        self.assertEqual(dest, ["a.csv", "b.csv"][shard_index("j0", 2)])

    def test_edge_chooser_accepts_grpc_edges(self) -> None:
        choose = make_shard_edge_chooser(2)
        job = parse_job_line(format_job_line("j0", 1.0, (Task(0, 0.0),)))
        edges = [
            parse_dial("127.0.0.1:50051,1", caller="server"),
            parse_dial("127.0.0.1:50052,1", caller="server"),
        ]
        dest = choose(job, [], edges, random.Random(0))
        self.assertEqual(dest, edges[shard_index("j0", 2)].address)


if __name__ == "__main__":
    unittest.main()
