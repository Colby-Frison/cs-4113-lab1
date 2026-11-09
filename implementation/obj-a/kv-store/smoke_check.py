#!/usr/bin/env python3
"""End-to-end smoke for persistence, sharding, replication, and N x M.

Runs a short launch, checks sink delivery, and verifies per-shard JSON keys
land on the expected shard. With M>=2, also checks replica JSON matches the
coordinator map for each shard.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

OBJ_A = Path(__file__).resolve().parents[1]
if str(OBJ_A) not in sys.path:
    sys.path.insert(0, str(OBJ_A))

from lib.kv_store import load_kv  # noqa: E402
from lib.kv_routing import shard_index  # noqa: E402

from launch import main as launch_main  # noqa: E402
from topology import build_topology  # noqa: E402


def verify_run(run_dir: Path, *, shards: int, replicas: int, jobs: int) -> None:
    topology = build_topology(run_dir, shards=shards, replicas=replicas)
    delivered = 0
    for plan in topology.plans:
        if plan.sink_csv.exists():
            for line in plan.sink_csv.read_text(encoding="utf-8").splitlines():
                if line.strip() and not line.lower().startswith("job"):
                    delivered += 1
    if delivered != jobs:
        raise SystemExit(f"expected {jobs} sink rows, got {delivered}")

    for plan in topology.plans:
        state = load_kv(plan.kv_json) if plan.kv_json.exists() else {}
        for key in state:
            if shard_index(key, shards) != plan.shard:
                raise SystemExit(f"key {key} stored on shard {plan.shard}, expected {shard_index(key, shards)}")
        if replicas >= 2:
            for replica_path in plan.replica_json:
                replica_state = load_kv(replica_path) if replica_path.exists() else {}
                if replica_state != state:
                    raise SystemExit(
                        f"replica {replica_path.name} diverged from coordinator "
                        f"{plan.kv_json.name}: {replica_state} vs {state}"
                    )
    print(json.dumps({"ok": True, "shards": shards, "replicas": replicas, "jobs": jobs}))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kv-smoke", description="KV store smoke checks.")
    parser.add_argument("--jobs", type=int, default=24)
    parser.add_argument("--wait", type=float, default=0.01)
    parser.add_argument(
        "--configs",
        default="1,1;2,1;1,2;2,2",
        help="Semicolon-separated N,M pairs (default: 1,1;2,1;1,2;2,2)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configs: list[tuple[int, int]] = []
    for piece in args.configs.split(";"):
        piece = piece.strip()
        if not piece:
            continue
        n_text, m_text = piece.split(",")
        configs.append((int(n_text), int(m_text)))
    base_port = 29000
    for index, (shards, replicas) in enumerate(configs):
        with tempfile.TemporaryDirectory(prefix=f"kv-smoke-n{shards}-m{replicas}-") as tmp:
            run_dir = Path(tmp)
            code = launch_main(
                [
                    "--run-dir",
                    str(run_dir),
                    "--shards",
                    str(shards),
                    "--replicas",
                    str(replicas),
                    "--jobs",
                    str(args.jobs),
                    "--wait",
                    str(args.wait),
                    "--sf-port0",
                    str(base_port + index * 40),
                    "--replica-port0",
                    str(base_port + 500 + index * 40),
                ]
            )
            if code != 0:
                return code
            verify_run(run_dir, shards=shards, replicas=replicas, jobs=args.jobs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
