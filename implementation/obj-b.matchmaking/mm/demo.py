"""File-edge demo of the monolith and of the six-stage pipeline.

Edges are ordinary files, so the run finishes at EOF and does not need FIFOs
or a gRPC package. ``workers`` in the topology is the gRPC pool a later launch
uses. This runner starts one thread per process. Ready-check retries are fed
back through the search queue as a new file each round.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from mm.ticket import parse_ticket

MM_ROOT = Path(__file__).resolve().parents[1]
OBJ_A = MM_ROOT.parent / "obj-a"
QUEUE = OBJ_A / "queue" / "queue.py"
GENERATOR = MM_ROOT / "workload-generator" / "workload_generator.py"
MONOLITH = MM_ROOT / "server-monolith" / "server_func.py"
STAGES = MM_ROOT / "server-stages" / "server_func.py"
BASELINE = MM_ROOT / "topology" / "baseline.json"
MONOLITH_TOPOLOGY = MM_ROOT / "topology" / "monolith.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the matchmaking file-edge demo.")
    parser.add_argument(
        "--which",
        choices=("monolith", "stages", "both"),
        default="both",
        help="Which topology to run (default: both).",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="Output directory (default: implementation/obj-b.matchmaking/runs/demo).",
    )
    parser.add_argument("--count", type=int, default=None, help="Override the topology job count.")
    parser.add_argument("--time-scale", type=float, default=None, help="Override --time-scale.")
    parser.add_argument("--seed", type=int, default=None, help="Override the generator and ready-check seed.")
    parser.add_argument("--arrival-mean", type=float, default=None, help="Override the unscaled interarrival mean.")
    parser.add_argument(
        "--wipe",
        action="store_true",
        help="Delete --workdir first. The default workdir is always replaced.",
    )
    args = parser.parse_args(argv)
    workdir = args.workdir or (MM_ROOT / "runs" / "demo")
    prepare_workdir(workdir, wipe=args.wipe or args.workdir is None)

    if args.which in ("monolith", "both"):
        run_monolith(workdir / "monolith", _load(MONOLITH_TOPOLOGY), args)
    if args.which in ("stages", "both"):
        run_stages(workdir / "stages", _load(BASELINE), args)
    return 0


def prepare_workdir(path: Path, *, wipe: bool) -> None:
    if wipe and path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    if any(path.iterdir()):
        raise SystemExit(f"{path} is not empty; pass --wipe or a new --workdir")


def run_monolith(root: Path, topology: dict, args: argparse.Namespace) -> None:
    """One queue, then the server that sleeps every stage."""
    root.mkdir(parents=True)
    jobs = root / "jobs.csv"
    _generate(jobs, topology["generator"], args)
    queue = topology["queue"]
    buf = root / f"{queue['id']}-buf.csv"
    _queue(queue, jobs, buf, root / "queue.log")
    sink = root / topology["sink"]
    server = topology["server"]
    _run(
        [
            sys.executable,
            str(MONOLITH),
            "-i",
            str(buf),
            "-o",
            str(sink),
            "--qid",
            server["qid"],
            "--server-id",
            str(server["server_id"]),
            "--logfile",
            str(root / "server.log"),
        ]
    )
    produced = _data_rows(jobs)
    matched = _data_rows(sink)
    print(f"monolith: {len(produced)} tickets, {len(matched)} matched -> {sink}")


def run_stages(root: Path, topology: dict, args: argparse.Namespace) -> None:
    """Six queues. Ready-check failures go through search again, then allocate."""
    root.mkdir(parents=True)
    jobs = root / "jobs.csv"
    _generate(jobs, topology["generator"], args)
    pipeline = topology["pipeline"]
    search = _step(pipeline, "search")
    ready = _step(pipeline, "ready")
    allocate = _step(pipeline, "allocate")

    feed = jobs
    for step in pipeline:
        stage = step["server"]["stage"]
        if stage in ("ready", "allocate"):
            break
        if stage == "route":
            routes = step["server"]["routes"]
            if len(routes) != 1:
                raise SystemExit("the file demo follows one region edge")
            region, queue_id = next(iter(routes.items()))
            server_out = root / f"{queue_id}-in.csv"
            extra = ["--route-edge", f"{region}={server_out}"]
        else:
            server_out = root / f"{stage}-out.csv"
            extra = ["-o", str(server_out)]
        feed = _hop(root, step, feed, server_out, extra, stage)

    pass_path = root / "ready-pass.csv"
    fail_path = root / topology["sinks"]["failed"]
    _ready_loop(root, feed, ready, search, pass_path, fail_path, topology, args)
    matched = root / topology["sinks"]["matched"]
    _hop(root, allocate, pass_path, matched, ["-o", str(matched)], "allocate")

    produced = _data_rows(jobs)
    ok = _data_rows(matched)
    failed = _data_rows(fail_path)
    print(
        f"stages: {len(produced)} tickets, {len(ok)} matched, {len(failed)} failed "
        f"-> {matched}"
    )


def _ready_loop(
    root: Path,
    feed: Path,
    ready: dict,
    search: dict,
    pass_path: Path,
    fail_path: Path,
    topology: dict,
    args: argparse.Namespace,
) -> None:
    max_visits = int(topology["max_ready_visits"])
    fail_p = topology["ready_fail_p"]
    seed = args.seed if args.seed is not None else topology["generator"]["seed"]
    state_path = root / "ready-state.txt"
    pending = feed
    for round_index in range(max_visits):
        if not _data_rows(pending):
            break
        buf = root / f"ready-r{round_index}-buf.csv"
        _queue(ready["queue"], pending, buf, root / f"q-ready-r{round_index}.log")
        retry = root / f"ready-r{round_index}-retry.csv"
        server = ready["server"]
        cmd = [
            sys.executable,
            str(STAGES),
            "--stage",
            "ready",
            "-i",
            str(buf),
            "--qid",
            ready["queue"]["id"],
            "--server-id",
            str(server["server_id"]),
            "--logfile",
            str(root / f"ready-r{round_index}.log"),
            "--pass",
            str(pass_path),
            "--retry",
            str(retry),
            "--fail",
            str(fail_path),
            "--ready-fail-p",
            str(fail_p),
            "--max-ready-visits",
            str(max_visits),
            "--state-file",
            str(state_path),
        ]
        if seed is not None:
            cmd.extend(["--seed", str(seed)])
        _run(cmd)
        retried = _data_rows(retry)
        if not retried:
            break
        if round_index + 1 >= max_visits:
            raise SystemExit(
                f"ready check wrote {len(retried)} retries on visit {round_index + 1} "
                f"with max {max_visits}"
            )
        search_out = root / f"search-r{round_index}-out.csv"
        _hop(
            root,
            search,
            retry,
            search_out,
            ["-o", str(search_out)],
            f"search-r{round_index}",
        )
        pending = search_out


def _hop(
    root: Path,
    step: dict,
    feed: Path,
    server_out: Path,
    extra: list[str],
    label: str,
) -> Path:
    buf = root / f"{label}-buf.csv"
    _queue(step["queue"], feed, buf, root / f"{label}-queue.log")
    server = step["server"]
    cmd = [
        sys.executable,
        str(STAGES),
        "--stage",
        server["stage"],
        "-i",
        str(buf),
        "--qid",
        step["queue"]["id"],
        "--server-id",
        str(server["server_id"]),
        "--logfile",
        str(root / f"{label}-server.log"),
        *extra,
    ]
    _run(cmd)
    return server_out


def _generate(path: Path, generator: dict, args: argparse.Namespace) -> None:
    count = args.count if args.count is not None else generator["count"]
    scale = args.time_scale if args.time_scale is not None else generator["time_scale"]
    seed = args.seed if args.seed is not None else generator.get("seed")
    arrival = args.arrival_mean if args.arrival_mean is not None else generator["arrival_mean"]
    cmd = [
        sys.executable,
        str(GENERATOR),
        "--out",
        str(path),
        "--count",
        str(count),
        "--time-scale",
        str(scale),
        "--arrival-mean",
        str(arrival),
    ]
    if seed is not None:
        cmd.extend(["--seed", str(seed)])
    if generator.get("region"):
        cmd.extend(["--region", generator["region"]])
    if generator.get("mode"):
        cmd.extend(["--mode", generator["mode"]])
    _run(cmd)


def _queue(queue: dict, infile: Path, outfile: Path, logfile: Path) -> None:
    _run(
        [
            sys.executable,
            str(QUEUE),
            "--qsize",
            str(queue["qsize"]),
            "--qid",
            queue["id"],
            "-i",
            str(infile),
            "-o",
            str(outfile),
            "--logfile",
            str(logfile),
        ]
    )


def _run(cmd: list[str]) -> None:
    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        script = cmd[1] if len(cmd) > 1 else cmd[0]
        tail = (completed.stderr or completed.stdout or "").strip()[-2000:]
        raise SystemExit(f"{script} failed with exit {completed.returncode}\n{tail}")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _step(pipeline: list[dict], stage: str) -> dict:
    for step in pipeline:
        if step["server"]["stage"] == stage:
            return step
    raise SystemExit(f"topology has no {stage} stage")


def _data_rows(path: Path) -> list[str]:
    if not path.exists():
        return []
    rows: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("Job Id,"):
            continue
        rows.append(line)
    return rows


def ticket_ids(rows: list[str]) -> list[str]:
    """Job id column from demo sink rows. Raises when a row is not a ticket."""
    ids: list[str] = []
    for row in rows:
        job_id = row.split(",", 1)[0]
        if parse_ticket(job_id) is None:
            raise ValueError(f"sink row is not a party ticket: {job_id}")
        ids.append(job_id)
    return ids
