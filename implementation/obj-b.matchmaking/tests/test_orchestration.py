"""The topology converter emits a gRPC stack and a FIFO stack."""

from __future__ import annotations

import unittest

import support

from orchestration.convert import build_plan, load_topology, render_compose, render_k8s

del support


class OrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.topology = load_topology()

    def test_grpc_chain_starts_the_generator_after_admit(self) -> None:
        plan = build_plan(self.topology, edge="grpc")
        by_name = {service.name: service for service in plan.services}
        self.assertEqual(plan.count, 8)
        self.assertEqual(by_name["s-search"].listen_port, 5007)
        self.assertIn("--ingrpc", by_name["s-search"].command)
        self.assertIn("0.0.0.0:5007,4", by_name["s-search"].command)
        self.assertIn("0.0.0.0:5006,4", by_name["q-search"].command)
        ready = by_name["s-ready"].command
        self.assertIn("q-allocate:5010,1", ready)
        self.assertIn("q-search:5006,1", ready)
        self.assertIn("/shared/failed.csv", ready)
        self.assertEqual(by_name["s-ready"].environment["WAIT_FOR"], "q-allocate:5010")
        self.assertNotIn("--ingrpc", by_name["q-admit"].command)
        self.assertIn("/fifos/jobs", by_name["q-admit"].command)
        self.assertIn("--count", by_name["generator"].command)
        self.assertIn("q-admit", by_name["generator"].depends_on)
        self.assertIn("fifo-hold", by_name["generator"].depends_on)
        self.assertNotIn("q-search", by_name["s-ready"].depends_on)

    def test_fifo_search_reads_the_route_and_the_retry(self) -> None:
        plan = build_plan(self.topology, edge="fifo")
        by_name = {service.name: service for service in plan.services}
        search = by_name["q-search"].command
        flags = [search[index + 1] for index, arg in enumerate(search) if arg == "-i"]
        self.assertEqual(
            flags,
            ["/fifos/s-route-to-q-search", "/fifos/s-ready-to-q-search"],
        )
        self.assertNotIn("--ingrpc", search)
        self.assertIn("/fifos/s-route-to-q-search", plan.fifos)
        self.assertTrue(all(service.name != "generator" or service.restart == "no" for service in plan.services))
        self.assertGreater(len(by_name["generator"].depends_on), 6)

    def test_continuous_omits_the_count(self) -> None:
        plan = build_plan(self.topology, edge="grpc", continuous=True)
        generator = next(service for service in plan.services if service.name == "generator")
        self.assertNotIn("--count", generator.command)
        self.assertIsNone(plan.count)

    def test_manifests_name_the_services(self) -> None:
        plan = build_plan(self.topology, edge="grpc")
        compose = render_compose(plan)
        k8s = render_k8s(plan)
        self.assertEqual(compose.count("\n    build:\n"), 1)
        self.assertIn("name: matchmaking-grpc", compose)
        self.assertIn("s-validate:5001,1", compose)
        self.assertIn("kind: Namespace", k8s)
        self.assertIn("kind: Job", k8s)
        self.assertIn("tcpSocket:", k8s)
        self.assertIn("claimName: matchmaking-fifos", k8s)
        self.assertIn("claimName: matchmaking-results", k8s)


if __name__ == "__main__":
    unittest.main()
