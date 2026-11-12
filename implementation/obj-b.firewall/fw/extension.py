"""What a firewall network adds to a packet routing run.

``run_local.run_network(..., extension=FirewallExtension())`` writes the access
list after the build, starts the monolith and validate server functions from this
application with the list, gives every generator the blocked share, and counts
``FIREWALL-DENY`` among the ways a packet can end. A spec without
``firewall_rules`` runs as plain packet routing.
"""

from __future__ import annotations

import sys
from pathlib import Path

from run_local import Extension

from fw.firewall import write_acl

SERVER = Path(__file__).resolve().parents[1] / "server_func.py"
CHECKS = ("monolith", "validate")


class FirewallExtension(Extension):
    drop_errors = ("FIREWALL-DENY",)

    def prepare(self, spec: dict, out: Path) -> None:
        if spec.get("firewall_rules"):
            write_acl(spec["firewall_rules"], out / "acl.tsv")

    def server_command(self, command: list, stage: str, spec: dict, server_bin: str | None) -> list:
        if not spec.get("firewall_rules") or stage not in CHECKS:
            return command
        if server_bin:
            raise SystemExit("the C++ server functions have no firewall")
        # Packet routing starts the monolith as [python, script, ...] and a stage as
        # [python, script, "--stage", stage, ...]. Keep the options after those.
        options = command[2:] if stage == "monolith" else command[4:]
        return [
            sys.executable, SERVER, "--stage", stage, *options,
            "--acl", "acl.tsv", "--rule-wait", spec.get("rule_wait", 0.0),
        ]

    def generator_args(self, spec: dict) -> list:
        return ["--blocked-share", spec["blocked_share"]] if "blocked_share" in spec else []
