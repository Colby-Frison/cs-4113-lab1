#!/usr/bin/env python3
"""Start the matchmaking pipeline from Windows or Linux.

The default command regenerates the Compose files from topology/baseline.json,
builds the image, starts every queue and stage server, and then starts the
workload generator. Sinks land in orchestration/runs/<edge>/.

    python implementation/obj-b.matchmaking/orchestration/run.py
    python implementation/obj-b.matchmaking/orchestration/run.py --edge fifo
    python implementation/obj-b.matchmaking/orchestration/run.py --continuous
    python implementation/obj-b.matchmaking/orchestration/run.py --down
    python implementation/obj-b.matchmaking/orchestration/run.py --kubernetes
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ORCH = Path(__file__).resolve().parent
MM_ROOT = ORCH.parent
if str(MM_ROOT) not in sys.path:
    sys.path.insert(0, str(MM_ROOT))

from orchestration.convert import (
    DEFAULT_TOPOLOGY,
    GENERATED,
    IMAGE,
    build_plan,
    load_topology,
    write_outputs,
)

IMPLEMENTATION = ORCH.parents[1]
DOCKER_DESKTOP = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Orchestrate the matchmaking pipeline.")
    parser.add_argument("--topology", type=Path, default=DEFAULT_TOPOLOGY)
    parser.add_argument("--edge", choices=("grpc", "fifo"), default="grpc")
    parser.add_argument("--continuous", action="store_true", help="Omit --count and keep generating.")
    parser.add_argument("--write-only", action="store_true", help="Regenerate manifests and do not start Docker.")
    parser.add_argument("--down", action="store_true", help="Stop the selected compose project and delete its volumes.")
    parser.add_argument("--kubernetes", action="store_true", help="Build the image and kubectl apply the refined manifest.")
    parser.add_argument("--kompose", action="store_true", help="Run kompose convert on the gRPC compose file.")
    args = parser.parse_args(argv)

    topology = load_topology(args.topology)
    written = write_outputs(topology, GENERATED, continuous=args.continuous)
    for path in written:
        print(path)
    if args.write_only:
        return 0
    if args.kompose:
        return _kompose(GENERATED / "compose.grpc.yml", GENERATED / "kompose")
    if args.kubernetes:
        return _kubernetes()
    _ensure_docker()
    compose = GENERATED / f"compose.{args.edge}.yml"
    if args.down:
        return _compose(compose, "down", "-v", "--remove-orphans")
    runs = ORCH / "runs" / args.edge
    _reset_runs(runs)
    _build_image()
    _compose(compose, "down", "-v", "--remove-orphans")
    _compose(compose, "up", "--force-recreate", "--remove-orphans", "-d")
    plan = build_plan(topology, edge=args.edge, continuous=args.continuous)
    if plan.count is None:
        print("The generator is streaming. Stop it with:")
        print(_down_command(args.edge))
        return 0
    return _wait_for_sinks(compose, runs, topology, plan.count)


def _wait_for_sinks(compose: Path, runs: Path, topology: dict, count: int) -> int:
    sinks = topology.get("sinks") or {}
    names = [str(sinks.get("matched", "matched.csv")), str(sinks.get("failed", "failed.csv"))]
    generator = topology.get("generator") or {}
    scale = float(generator.get("time_scale", 0.05))
    arrival = float(generator.get("arrival_mean", 14))
    deadline = time.monotonic() + 120 + count * (arrival + 30) * max(scale, 0.001)
    while time.monotonic() < deadline:
        counts = {name: _rows(runs / name) for name in names}
        done = sum(counts.values())
        print(f"sinks {done}/{count} " + " ".join(f"{name}={counts[name]}" for name in names))
        if done >= count:
            print(f"Finished. Ticket rows are in {runs}")
            print("The containers are still up. Stop them with:")
            print(_down_command(_edge_of(compose)))
            return 0
        time.sleep(2)
    print("Timed out before every ticket reached a sink.", file=sys.stderr)
    subprocess.run(["docker", "compose", "-f", str(compose), "ps", "-a"], check=False)
    subprocess.run(["docker", "compose", "-f", str(compose), "logs", "--tail", "80"], check=False)
    return 1


def _down_command(edge: str) -> str:
    flag = "" if edge == "grpc" else f" --edge {edge}"
    return f"python {ORCH / 'run.py'}{flag} --down"


def _edge_of(compose: Path) -> str:
    # compose.grpc.yml -> grpc
    return compose.stem.split(".", 1)[1]


def _rows(path: Path) -> int:
    if not path.is_file():
        return 0
    rows = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("Job Id"):
            rows += 1
    return rows


def _reset_runs(runs: Path) -> None:
    runs.mkdir(parents=True, exist_ok=True)
    for path in runs.iterdir():
        if path.is_file():
            path.unlink()


def _build_image() -> None:
    subprocess.run(
        ["docker", "build", "-f", str(ORCH / "Dockerfile"), "-t", IMAGE, str(IMPLEMENTATION)],
        check=True,
    )


def _kubernetes() -> int:
    _ensure_docker()
    if shutil.which("kubectl") is None:
        raise SystemExit("kubectl is not on PATH. The refined manifest is orchestration/generated/k8s.yaml.")
    _build_image()
    subprocess.run(["kubectl", "apply", "-f", str(GENERATED / "k8s.yaml")], check=True)
    print("Applied the matchmaking namespace. The generator Job starts after the admit queue opens its FIFO.")
    return 0


def _kompose(compose: Path, out_dir: Path) -> int:
    tool = shutil.which("kompose")
    if tool is None:
        raise SystemExit("kompose is not on PATH. Install it, then rerun with --kompose.")
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([tool, "convert", "-f", str(compose), "-o", str(out_dir)], check=True)
    print(out_dir)
    return 0


def _compose(compose: Path, *args: str) -> int:
    command = ["docker", "compose", "-f", str(compose), *args]
    print(" ".join(command))
    subprocess.run(command, check=True)
    return 0


def _ensure_docker() -> None:
    if _docker_ready():
        return
    if sys.platform == "win32" and DOCKER_DESKTOP.is_file():
        print("Starting Docker Desktop.")
        subprocess.Popen([str(DOCKER_DESKTOP)])
        for _ in range(90):
            time.sleep(2)
            if _docker_ready():
                return
    raise SystemExit("Docker is not running. Start Docker Desktop, then run this command again.")


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


if __name__ == "__main__":
    raise SystemExit(main())
