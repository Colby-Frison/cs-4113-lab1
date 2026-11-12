"""The firewall: matching, the generated list, visits, traffic, the plan, and a whole network."""

from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path

from support import APP, load_script
from test_router import Harness, packet

from lib.job import ipv4_to_int
from pk.router import LOOKUP_TID
from pk.routing import router_network

from fw.firewall import Firewall, Rule, parse_address, write_acl
from fw.router import FirewallRouter

try:
    import grpc  # noqa: F401

    HAVE_GRPC = True
except ImportError:
    HAVE_GRPC = False

GENERATOR = load_script(APP / "workload-generator" / "workload_generator.py", "pk_generator_fw")
STUDIES = APP.parents[1] / "analysis"
STUDY = load_script(STUDIES / "obj-b-packets" / "run_study.py", "pk_run_study_fw")


def fw_harness(firewall: Firewall, rule_wait: float, **kwargs) -> Harness:
    """The packet routing test harness, with a firewall router in place of the router."""
    harness = Harness(**kwargs)
    plain = harness.router
    harness.router = FirewallRouter(
        plain.qid,
        plain.server_id,
        plain.log,
        addresses=plain.addresses,
        table=plain.table,
        sink=plain.sink,
        next_qid=plain.next_qid,
        sleep_fn=plain.sleep_fn,
        submit_fn=plain.submit_fn,
        firewall=firewall,
        rule_wait=rule_wait,
    )
    return harness


def rule(address: str, mask: str, action: str) -> Rule:
    return Rule(parse_address(address), parse_address(mask), action)


def listed(rules: int) -> Firewall:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "acl.tsv"
        write_acl(rules, path)
        return Firewall.load(path)


class MatchTests(unittest.TestCase):
    def test_prefix_and_wildcard_masks(self) -> None:
        prefix = rule("10.0.5.0", "255.255.255.0", "deny")
        wildcard = rule("10.0.0.240", "255.0.0.240", "deny")
        self.assertTrue(prefix.matches(ipv4_to_int("10.0.5.77")))
        self.assertFalse(prefix.matches(ipv4_to_int("10.0.6.77")))
        self.assertTrue(wildcard.matches(ipv4_to_int("10.3.7.243")))
        self.assertTrue(wildcard.matches(ipv4_to_int("10.0.0.255")))
        self.assertFalse(wildcard.matches(ipv4_to_int("10.3.7.239")))

    def test_first_match_wins_and_counts_the_rules_checked(self) -> None:
        firewall = Firewall([
            rule("10.0.1.0", "255.255.255.0", "allow"),
            rule("10.0.0.0", "255.0.0.0", "deny"),
        ])
        self.assertEqual(firewall.check(ipv4_to_int("10.0.1.9")), (True, 1))
        self.assertEqual(firewall.check(ipv4_to_int("10.0.2.9")), (False, 2))

    def test_no_match_allows_after_checking_every_rule(self) -> None:
        firewall = Firewall([rule("10.0.1.0", "255.255.255.0", "deny")] * 3)
        self.assertEqual(firewall.check(ipv4_to_int("192.168.0.1")), (True, 3))

    def test_a_bad_line_names_its_place(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acl.tsv"
            path.write_text("10.0.0.0\t255.0.0.0\tdrop\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, ":1:"):
                Firewall.load(path)


class ListTests(unittest.TestCase):
    def test_n_rules_and_the_blocking_rule_comes_last(self) -> None:
        firewall = listed(50)
        self.assertEqual(len(firewall.rules), 50)
        self.assertEqual(firewall.rules[-1], rule("10.0.0.240", "255.0.0.240", "deny"))

    def test_every_packet_checks_every_rule(self) -> None:
        firewall = listed(50)
        for router in (0, 1, 255, 256, 6473):
            for host, allowed in ((1, True), (239, True), (240, False), (254, False)):
                self.assertEqual(firewall.check(router_network(router) | host), (allowed, 50))

    def test_one_rule_is_just_the_blocking_rule(self) -> None:
        self.assertEqual(len(listed(1).rules), 1)
        with self.assertRaises(ValueError):
            listed(0)


class VisitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.firewall = listed(50)

    def test_a_denied_packet_pays_the_check_and_is_not_forwarded(self) -> None:
        harness = fw_harness(self.firewall, 0.0002)
        harness.router.monolith(packet(dst="10.0.1.245"))
        self.assertEqual(harness.slept[:3], [0.01, 0.02, 0.03])
        self.assertAlmostEqual(harness.slept[3], 50 * 0.0002)
        self.assertEqual(harness.sent, [])
        self.assertEqual(harness.kinds[-2:], ["EXITS-SERVER", "ERROR FIREWALL-DENY"])

    def test_an_allowed_packet_pays_the_check_and_is_forwarded(self) -> None:
        harness = fw_harness(self.firewall, 0.0002)
        harness.router.monolith(packet(dst="10.0.1.5"))
        self.assertAlmostEqual(harness.slept[3], 50 * 0.0002)
        self.assertEqual(len(harness.sent), 1)
        self.assertEqual(harness.kinds[-2:], ["EXITS-SERVER", "DEPART-VIA"])

    def test_the_firewall_checks_before_the_ttl(self) -> None:
        harness = fw_harness(self.firewall, 0.0)
        harness.router.monolith(packet(dst="10.0.1.245", ttl=0))
        self.assertIn("FIREWALL-DENY", harness.messages[-1])
        self.assertFalse(any("TTL-EXPIRED" in message for message in harness.messages))

    def test_validate_checks_and_lookup_does_not(self) -> None:
        validate = fw_harness(self.firewall, 0.0002, next_qid="q7")
        validate.router.validate(packet(dst="10.0.1.245"))
        self.assertEqual(validate.sent, [])
        self.assertIn("FIREWALL-DENY", validate.messages[-1])

        lookup = fw_harness(self.firewall, 0.0002, next_qid="q8")
        lookup.router.lookup(packet(dst="10.0.1.245"))
        self.assertEqual(lookup.slept, [task.wait_seconds for task in packet().tasks if task.tid == LOOKUP_TID])
        self.assertEqual(len(lookup.sent), 1)

    def test_no_firewall_is_the_packet_routing_visit(self) -> None:
        harness = Harness()
        harness.router.monolith(packet(dst="10.0.1.245"))
        self.assertEqual(harness.slept, [0.01, 0.02, 0.03])
        self.assertEqual(len(harness.sent), 1)


class TrafficTests(unittest.TestCase):
    def test_the_blocked_share_is_close_to_p(self) -> None:
        rng = random.Random(1)
        hosts = [GENERATOR.draw_destination(rng, 3, 10, 0.1) & 255 for _ in range(20000)]
        blocked = sum(host >= 240 for host in hosts) / len(hosts)
        self.assertAlmostEqual(blocked, 0.1, delta=0.01)
        self.assertTrue(all(1 <= host <= 254 for host in hosts))

    def test_without_the_option_destinations_are_unchanged(self) -> None:
        before, after = random.Random(5), random.Random(5)
        for _ in range(1000):
            router = before.randrange(9)
            router += router >= 3
            expected = router_network(router) | before.randint(1, 254)
            self.assertEqual(GENERATOR.draw_destination(after, 3, 10), expected)


class PlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.packets = json.loads((STUDIES / "obj-b-packets" / "simple" / "study.json").read_text())
        cls.firewall = json.loads((STUDIES / "obj-b-firewall" / "simple" / "study.json").read_text())

    def test_without_a_firewall_the_plan_is_the_packet_routing_plan(self) -> None:
        plain = STUDY.plan_study(self.packets)
        empty = STUDY.plan_study(dict(self.packets, firewall_rules=0, rule_wait=0.0002, blocked_share=0.1))
        self.assertEqual(plain["sets"], empty["sets"])
        self.assertEqual(plain["rate"], empty["rate"])

    def test_monolith_load_follows_the_formula(self) -> None:
        plan = STUDY.plan_study(self.firewall)
        study, p = self.firewall, self.firewall["blocked_share"]
        work = study["validate_wait"] + study["lookup_wait"] + study["forward_wait_mean"]
        work += study["firewall_rules"] * study["rule_wait"]
        servers = plan["sets"]["base"]["servers"]
        sources = [r for r in range(study["routers"]) if plan["sets"]["base"]["predicted_utilization"][r]
                   * servers[r] / plan["rate"] / work > (1 - p) * plan["visits"][r] + 1e-12]
        self.assertEqual(len(sources), study["sources"])
        for router in range(study["routers"]):
            share = 1 / study["sources"] if router in sources else 0.0
            expected = plan["rate"] * ((1 - p) * plan["visits"][router] + p * share) * work / servers[router]
            self.assertAlmostEqual(plan["sets"]["base"]["predicted_utilization"][router], expected)
        self.assertAlmostEqual(max(plan["sets"]["base"]["predicted_utilization"]), 0.7)

    def test_stages_add_the_check_to_validate_only(self) -> None:
        plan = STUDY.plan_study(dict(self.firewall, mode="stages"))
        study, p = self.firewall, self.firewall["blocked_share"]
        load = plan["sets"]["base"]["predicted_utilization"]
        servers = plan["sets"]["base"]["servers"]
        check = study["firewall_rules"] * study["rule_wait"]
        router = 1
        allowed = plan["rate"] * (1 - p) * plan["visits"][router] / servers[router]
        self.assertAlmostEqual(load[3 * router + 1], allowed * study["lookup_wait"])
        self.assertAlmostEqual(load[3 * router + 2], allowed * study["forward_wait_mean"])
        self.assertGreaterEqual(load[3 * router] * servers[router] / plan["rate"],
                                (1 - p) * plan["visits"][router] * (study["validate_wait"] + check) - 1e-12)

    def test_every_firewall_model_has_the_settings_and_a_slurm_job(self) -> None:
        for model in ("simple", "medium", "large", "very-large"):
            with self.subTest(model=model):
                folder = STUDIES / "obj-b-firewall" / model
                study = json.loads((folder / "study.json").read_text())
                packets = json.loads((STUDIES / "obj-b-packets" / model / "study.json").read_text())
                for key in ("routers", "sources", "baseline_servers", "packets", "sets"):
                    self.assertEqual(study[key], packets[key])
                self.assertEqual(study["firewall_rules"], 50)
                self.assertEqual(study["blocked_share"], 0.1)
                # The list adds the same share of work at every scale.
                self.assertAlmostEqual(
                    study["firewall_rules"] * study["rule_wait"] / study["validate_wait"], 2.0
                )
                script = (folder / "run.sbatch").read_text()
                self.assertIn(f'job.sh" obj-b-firewall/{model} ', script)


@unittest.skipUnless(HAVE_GRPC, "grpcio is not installed")
class NetworkTests(unittest.TestCase):
    def run_firewalled(self, mode: str) -> dict:
        import run_local

        from fw.extension import FirewallExtension

        spec = json.loads((APP / "spec.json").read_text())
        spec.update(routers=4, sources=2, firewall_rules=20, rule_wait=0.0002, blocked_share=0.3,
                    total_servers=12, buffer_servers=12, traffic_seed=3)
        with tempfile.TemporaryDirectory() as directory:
            summary = run_local.run_network(
                spec, mode, Path(directory) / "run", 30.0, packets=240, extension=FirewallExtension()
            )
            self.assertTrue((Path(directory) / "run" / "acl.tsv").exists())
        return summary

    def check(self, summary: dict) -> None:
        self.assertEqual(summary["sent"], 240)
        self.assertEqual(summary["unaccounted"], 0)
        self.assertEqual(summary["QUEUE-FULL"] + summary["TTL-EXPIRED"] + summary["NO-OUT-EDGE"], 0)
        self.assertEqual(summary["delivered"] + summary["FIREWALL-DENY"], 240)
        self.assertAlmostEqual(summary["FIREWALL-DENY"] / 240, 0.3, delta=0.1)

    def test_monolith_network_denies_the_blocked_share(self) -> None:
        self.check(self.run_firewalled("monolith"))

    def test_stage_network_denies_the_blocked_share(self) -> None:
        self.check(self.run_firewalled("stages"))


if __name__ == "__main__":
    unittest.main()
