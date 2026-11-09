#!/usr/bin/env python3
"""Launch N shard coordinators and optional M replicas for one KV run.

Startup order: replicas, then stateful server functions (``--ingrpc``), then
queues. File sinks are opened by the server functions. Callers wait on sink
row counts, then stop the children.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

OBJ_A = Path(__file__).resolve().parents[1]
REPO = OBJ_A.parents[1]
if str(OBJ_A) not in sys.path:
    sys.path.insert(0, str(OBJ_A))

from topology import Topology, ShardPlan, build_topology, write_partitioned_jobs  # noqa: E402

QUEUE_PY = OBJ_A / "queue" / "queue.py"
SF_PY = OBJ_A / "server-func-stateful" / "server_func.py"
REPLICA_PY = OBJ_A / "kv-store" / "replica.py"
GENERATOR_PY = OBJ_A / "workload-generator" / "workload_generator.py"


def spawn(args: list[str], err_path: Path) -> subprocess.Popen:
    err_path.parent.mkdir(parents=True, exist_ok=True)
    handle = err_path.open("w", encoding="utf-8")
    return subprocess.Popen(
        args,
        cwd=str(REPO),
        stdout=subprocess.DEVNULL,
        stderr=handle,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def stop(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is None:
            process.kill()
    for process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def wait_for_listen(log_path: Path, timeout_sec: float) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if log_path.exists() and "gRPC listen" in log_path.read_text(encoding="utf-8", errors="replace"):
            return
        time.sleep(0.05)
    raise TimeoutError(f"timed out waiting for gRPC listen in {log_path}")


def replica_args(plan: ShardPlan, index: int) -> list[str]:
    port = plan.replica_ports[index]
    args = [
        sys.executable,
        str(REPLICA_PY),
        "--listen",
        f"127.0.0.1:{port},2",
        "--logfile",
        str(plan.replica_logs[index]),
        "--store-kv-to",
        str(plan.replica_json[index]),
    ]
    if plan.replica_json[index].exists():
        args.extend(["--load-kv-from", str(plan.replica_json[index])])
    return args


def sf_args(plan: ShardPlan, topology: Topology, *, workers: int) -> list[str]:
    args = [
        sys.executable,
        str(SF_PY),
        "--ingrpc",
        f"127.0.0.1:{plan.sf_port},{workers}",
        "-o",
        str(plan.sink_csv),
        "--logfile",
        str(plan.sf_log),
        "--qid",
        f"q{plan.shard}",
        "--store-kv-to",
        str(plan.kv_json),
    ]
    if plan.kv_json.exists():
        args.extend(["--load-kv-from", str(plan.kv_json)])
    if topology.uses_2pc:
        peers = ",".join(f"127.0.0.1:{port}" for port in plan.replica_ports)
        args.extend(["--kv-replicas", peers])
    return args


def queue_args(plan: ShardPlan, *, qsize: int, workers: int) -> list[str]:
    return [
        sys.executable,
        str(QUEUE_PY),
        "--qsize",
        str(qsize),
        "-i",
        str(plan.jobs_csv),
        "--outgrpc",
        f"127.0.0.1:{plan.sf_port},{workers}",
        "--logfile",
        str(plan.q_log),
        "--qid",
        f"q{plan.shard}",
    ]


def start_topology(
    topology: Topology,
    *,
    qsize: int,
    workers: int,
    listen_timeout_sec: float = 30.0,
) -> list[subprocess.Popen]:
    """Start replicas, then SFs, then queues. Return all child processes."""
    children: list[subprocess.Popen] = []
    try:
        if topology.uses_2pc:
            for plan in topology.plans:
                for index in range(topology.replicas):
                    children.append(spawn(replica_args(plan, index), plan.replica_logs[index].with_suffix(".err")))
            for plan in topology.plans:
                for index in range(topology.replicas):
                    wait_for_listen(plan.replica_logs[index], listen_timeout_sec)
        for plan in topology.plans:
            children.append(spawn(sf_args(plan, topology, workers=workers), plan.sf_log.with_suffix(".err")))
        for plan in topology.plans:
            wait_for_listen(plan.sf_log, listen_timeout_sec)
        for plan in topology.plans:
            children.append(spawn(queue_args(plan, qsize=qsize, workers=workers), plan.q_log.with_suffix(".err")))
        return children
    except Exception:
        stop(children)
        raise


def count_sink_jobs(topology: Topology) -> int:
    total = 0
    for plan in topology.plans:
        if not plan.sink_csv.exists():
            continue
        for line in plan.sink_csv.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lower().startswith("job"):
                total += 1
    return total


def wait_for_sinks(topology: Topology, expected: int, timeout_sec: float) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if count_sink_jobs(topology) >= expected:
            return
        time.sleep(0.05)
    got = count_sink_jobs(topology)
    raise TimeoutError(f"sinks have {got}/{expected} jobs")


def generate_jobs(path: Path, *, count: int, wait: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(
        [
            sys.executable,
            str(GENERATOR_PY),
            "--arrival-dist",
            "fixed",
            "--arrival-fixed",
            "0",
            "--wait-dist",
            "fixed",
            "--wait-fixed",
            str(wait),
            "--count",
            str(count),
            "--out",
            str(path),
        ],
        cwd=str(REPO),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kv-launch", description="Run one sharded/replicated KV pass.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--shards", type=int, default=2)
    parser.add_argument("--replicas", type=int, default=1)
    parser.add_argument("--jobs", type=int, default=40)
    parser.add_argument("--wait", type=float, default=0.01)
    parser.add_argument("--qsize", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--sf-port0", type=int, default=27000)
    parser.add_argument("--replica-port0", type=int, default=28000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.shards < 1 or args.replicas < 1:
        raise SystemExit("shards and replicas must be >= 1")
    run_dir = args.run_dir
    if run_dir.exists():
        for child in run_dir.iterdir():
            if child.is_file():
                child.unlink()
    run_dir.mkdir(parents=True, exist_ok=True)
    topology = build_topology(
        run_dir,
        shards=args.shards,
        replicas=args.replicas,
        sf_port0=args.sf_port0,
        replica_port0=args.replica_port0,
    )
    all_jobs = run_dir / "jobs-all.csv"
    generate_jobs(all_jobs, count=args.jobs, wait=args.wait)
    write_partitioned_jobs(all_jobs, topology)
    children = start_topology(topology, qsize=args.qsize, workers=args.workers)
    try:
        wait_for_sinks(topology, args.jobs, timeout_sec=max(30.0, args.jobs * args.wait * 4 + 20))
    finally:
        stop(children)
    print(f"ok shards={args.shards} replicas={args.replicas} jobs={args.jobs} sinks={count_sink_jobs(topology)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
