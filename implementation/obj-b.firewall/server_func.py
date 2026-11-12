#!/usr/bin/env python3
"""Router server function with a firewall, for the monolith and the validate stage.

It takes the packet routing server function's options, plus ``--stage monolith`` or
``--stage validate``, ``--acl``, and ``--rule-wait``. Lookup and forward stages use
the packet routing server function unchanged.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent / "obj-b.packets"), str(_HERE.parent / "obj-a")]

from pk.runtime import MONOLITH, run_cli  # noqa: E402

from fw.firewall import Firewall  # noqa: E402
from fw.router import FirewallRouter  # noqa: E402

STAGES = (MONOLITH, "validate")


def add_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--acl", required=True, help="Firewall access list")
    parser.add_argument("--rule-wait", type=float, default=0.0, help="Seconds per rule checked")


def make_router(args, stage, *router_args, **router_kwargs) -> FirewallRouter:
    return FirewallRouter(
        *router_args, firewall=Firewall.load(args.acl), rule_wait=args.rule_wait, **router_kwargs
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    chooser = argparse.ArgumentParser(add_help=False)
    chooser.add_argument("--stage", required=True, choices=STAGES)
    known, _ = chooser.parse_known_args(argv)
    description = "Router server function with a firewall."
    if known.stage == MONOLITH:
        # The packet routing monolith takes no --stage option.
        rest = [a for i, a in enumerate(argv) if a != "--stage" and (i == 0 or argv[i - 1] != "--stage")]
        return run_cli(MONOLITH, rest, description, add_options=add_options, make_router=make_router)
    return run_cli(None, argv, description, add_options=add_options, make_router=make_router)


if __name__ == "__main__":
    raise SystemExit(main())
