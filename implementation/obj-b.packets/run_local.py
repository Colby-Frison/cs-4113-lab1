#!/usr/bin/env python3
"""Build a small network and run it on this machine, one process per queue.

Order: every server function, then every queue, each waiting for ``gRPC listen``,
then the generators over FIFOs. A run ends when every packet sent is accounted for:
delivered to a sink, or dropped with a logged error. The packet accounting is
written to ``summary.json``. ``--server-bin`` runs the C++ server function instead
of the Python one.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

_APP = Path(__file__).resolve().parent
_OBJ_A = _APP.parent / "obj-a"
sys.path[:0] = [str(_APP), str(_OBJ_A)]

import build_network
from pk.routing import load_addresses, loopback_hosts

QUEUE = _OBJ_A / "queue" / "queue.py"
GENERATOR = _APP / "workload-generator" / "workload_generator.py"
MONOLITH_SERVER = _APP / "server-monolith" / "server_func.py"
STAGE_SERVER = _APP / "server-stages" / "server_func.py"

# A queue's listen pool only admits or rejects, so a few threads are enough.
QUEUE_LISTEN_WORKERS = 4
READY_TIMEOUT_SEC = 60
# Large networks start thousands of processes, so the wait grows with their number.
READY_SEC_PER_PROCESS = 0.05
DROP_ERRORS = ("QUEUE-FULL", "TTL-EXPIRED", "NO-OUT-EDGE")


class Extension:
    """What another application can add to a run. This base adds nothing.

    The firewall application's ``fw.extension.FirewallExtension`` is one.
    """

    drop_errors: tuple[str, ...] = ()

    def prepare(self, spec: dict, out: Path) -> None:
        """Called after the build, before any process starts."""

    def server_command(self, command: list, stage: str, spec: dict, server_bin: str | None) -> list:
        """The command that starts one server function."""
        return command

    def generator_args(self, spec: dict) -> list:
        """Extra options for every generator."""
        return []


class Run:
    """The processes of one run and where they log."""

    def __init__(self, out: Path) -> None:
        self.out = out
        self.processes: dict[str, subprocess.Popen] = {}

    def start(self, name: str, command: list[str]) -> subprocess.Popen:
        stderr = (self.out / f"{name}.stderr").open("w", encoding="utf-8")
        process = subprocess.Popen(
            [str(part) for part in command],
            stdout=stderr,
            stderr=stderr,
            cwd=self.out,
        )
        stderr.close()
        self.processes[name] = process
        return process

    def check_alive(self, names: list[str]) -> None:
        for name in names:
            status = self.processes[name].poll()
            if status is not None:
                detail = (self.out / f"{name}.stderr").read_text(encoding="utf-8")[-800:]
                raise SystemExit(f"{name} exited with {status}:\n{detail}")

    def wait_for_listen(self, names: list[str]) -> None:
        deadline = time.time() + READY_TIMEOUT_SEC + READY_SEC_PER_PROCESS * len(names)
        waiting = set(names)
        while waiting:
            if time.time() > deadline:
                raise SystemExit(f"no gRPC listen line from: {sorted(waiting)[:5]}")
            self.check_alive(sorted(waiting))
            waiting = {name for name in waiting if not self._is_listening(name)}
            time.sleep(0.05 if len(waiting) < 100 else 0.5)

    def _is_listening(self, name: str) -> bool:
        # The listen line is near the top, so a long log is not read in full.
        try:
            with (self.out / f"{name}.log").open(encoding="utf-8", errors="replace") as handle:
                return "gRPC listen" in handle.read(8192)
        except FileNotFoundError:
            return False

    def interrupt(self, names: list[str], timeout: float = 15.0) -> None:
        for name in names:
            if self.processes[name].poll() is None:
                self.processes[name].send_signal(signal.SIGINT)
        deadline = time.time() + timeout
        for name in names:
            try:
                self.processes[name].wait(timeout=max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                self.processes[name].kill()
                self.processes[name].wait()

    def kill_all(self) -> None:
        for process in self.processes.values():
            if process.poll() is None:
                process.kill()
                process.wait()


def server_command(
    server_bin: str | None, entry: dict, stage: str, qid: str, next_qid: str | None, address
) -> list:
    if server_bin:
        command = [server_bin, "--stage", stage]
    elif stage == "monolith":
        command = [sys.executable, MONOLITH_SERVER]
    else:
        command = [sys.executable, STAGE_SERVER, "--stage", stage]
    command += [
        "--qid", qid,
        "--ingrpc", f"{address.host}:{address.server_port},{entry['servers']}",
        "--addresses", "addresses.csv",
        "--logfile", f"sf-{qid}.log",
    ]
    if stage in ("monolith", "lookup"):
        command += ["--routes", f"routes/{qid}.tsv"]
    if stage in ("monolith", "forward"):
        command += ["--sink", f"sink-{qid}.csv"]
    if next_qid is not None:
        command += ["--next-qid", next_qid]
    return command


def start_servers(
    run: Run, server_bin: str | None, topology: dict, addresses: dict, extension: Extension
) -> list[str]:
    names = []
    for entry in topology["routers"]:
        queues = list(entry["queues"].items())
        for position, (stage, qid) in enumerate(queues):
            next_qid = queues[position + 1][1] if position + 1 < len(queues) else None
            name = f"sf-{qid}"
            command = server_command(server_bin, entry, stage, qid, next_qid, addresses[qid])
            run.start(name, extension.server_command(command, stage, topology["spec"], server_bin))
            names.append(name)
    return names


def start_queues(run: Run, topology: dict, addresses: dict) -> list[str]:
    names = []
    for entry in topology["routers"]:
        ingress = next(iter(entry["queues"].values()))
        for qid in entry["queues"].values():
            address = addresses[qid]
            command = [
                sys.executable, QUEUE,
                "--qid", qid,
                "--qsize", entry["qsize"],
                "--ingrpc", f"{address.host}:{address.queue_port},{QUEUE_LISTEN_WORKERS}",
                "--outgrpc", f"{address.host}:{address.server_port},{entry['servers']}",
                "--logfile", f"{qid}.log",
            ]
            if entry["source"] and qid == ingress:
                fifo = run.out / f"gen-{qid}.fifo"
                os.mkfifo(fifo)
                command += ["-i", fifo.name]
            name = qid
            run.start(name, command)
            names.append(name)
    return names


def start_generators(
    run: Run,
    topology: dict,
    rate: float,
    duration: float | None,
    packets: int | None,
    extension: Extension,
) -> list[str]:
    """Each source gets an equal share of the rate, and of the packets if there is a count."""
    spec = topology["spec"]
    sources = [entry for entry in topology["routers"] if entry["source"]]
    names = []
    for position, entry in enumerate(sources):
        if packets is not None:
            count = packets // len(sources) + (position < packets % len(sources))
            limit = ["--count", count]
        else:
            limit = ["--duration", duration]
        qid = next(iter(entry["queues"].values()))
        name = f"gen-{qid}"
        run.start(
            name,
            [
                sys.executable, GENERATOR,
                "--source", entry["router"],
                "--routers", len(topology["routers"]),
                "--rate", rate / len(sources),
                *limit,
                "--ttl", spec["ttl"],
                "--validate-wait", spec["validate_wait"],
                "--lookup-wait", spec["lookup_wait"],
                "--forward-wait-mean", spec["forward_wait_mean"],
                "--seed", spec.get("traffic_seed", spec["seed"]),
                *extension.generator_args(spec),
                "--out", f"{name}.fifo",
                "--copy", f"jobs-{qid}.csv",
            ],
        )
        names.append(name)
    return names


def count_lines(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def account(out: Path, errors: tuple[str, ...] = DROP_ERRORS) -> dict[str, int]:
    """Count every packet sent, and how each one ended."""
    counts = {"sent": 0, "delivered": 0, **{error: 0 for error in errors}}
    for path in out.glob("jobs-*.csv"):
        counts["sent"] += max(0, count_lines(path) - 1)
    for path in out.glob("sink-*.csv"):
        counts["delivered"] += count_lines(path)
    for path in out.glob("*.log"):
        text = path.read_text(encoding="utf-8")
        for error in errors:
            counts[error] += text.count(f" ERROR {error}")
    return counts


def wait_for_packets(
    run: Run, generators: list[str], timeout: float, errors: tuple[str, ...] = DROP_ERRORS
) -> dict[str, int]:
    deadline = time.time() + timeout
    pause = 0.2
    while True:
        if all(run.processes[name].poll() is not None for name in generators):
            started = time.time()
            counts = account(run.out, errors)
            ended = counts["delivered"] + sum(counts[error] for error in errors)
            if ended >= counts["sent"]:
                return counts
            if time.time() > deadline:
                print(f"timed out with {counts['sent'] - ended} packets unaccounted for")
                return counts
            # Counting reads every log, so a large run is not counted continuously.
            pause = max(0.2, 2 * (time.time() - started))
        time.sleep(pause)


def run_network(
    spec: dict,
    mode: str,
    out: Path,
    rate: float,
    *,
    duration: float | None = None,
    packets: int | None = None,
    server_bin: str | None = None,
    extension: Extension | None = None,
) -> dict:
    """Build one network in ``out``, run it to the end, and return its packet accounting.

    An ``extension`` lets another application add to the run, such as the firewall.
    """
    extension = extension or Extension()
    errors = DROP_ERRORS + extension.drop_errors
    out = Path(out).resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    per_router = len(build_network.STAGE_NAMES) if mode == build_network.STAGES else 1
    hosts = loopback_hosts(spec["routers"] * per_router)
    topology = build_network.build(spec, mode, out, hosts)
    extension.prepare(spec, out)
    addresses = load_addresses(out / "addresses.csv")
    expected = duration if duration is not None else packets / rate

    run = Run(out)
    started = time.time()
    try:
        servers = start_servers(run, server_bin, topology, addresses, extension)
        run.wait_for_listen(servers)
        queues = start_queues(run, topology, addresses)
        run.wait_for_listen(queues)
        generators = start_generators(run, topology, rate, duration, packets, extension)
        counts = wait_for_packets(run, generators, 1.1 * expected + 120, errors)
        run.interrupt(queues)
        run.interrupt(servers)
    finally:
        run.kill_all()

    summary = {
        "mode": mode,
        "server": "c++" if server_bin else "python",
        "routers": spec["routers"],
        "links": topology["links"],
        "mean_hops": round(topology["mean_hops"], 3),
        "longest_path_hops": topology["longest_path_hops"],
        "seconds": round(time.time() - started, 1),
        **counts,
        "unaccounted": counts["sent"] - counts["delivered"] - sum(counts[e] for e in errors),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--spec", default=str(_APP / "spec.json"))
    parser.add_argument("--mode", choices=(build_network.MONOLITH, build_network.STAGES), default="monolith")
    parser.add_argument("--routers", type=int, default=10)
    parser.add_argument("--sources", type=int, default=3)
    parser.add_argument("--rate", type=float, default=10.0, help="Total packets per second")
    parser.add_argument("--duration", type=float, default=5.0, help="Generator seconds")
    parser.add_argument("--server-bin", help="C++ packet_server binary")
    parser.add_argument("--out", required=True)
    parser.add_argument("--analyze", action="store_true", help="Run the Objective A analysis tools")
    args = parser.parse_args(argv)

    out = Path(args.out).resolve()
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    spec["routers"] = args.routers
    spec["sources"] = args.sources
    summary = run_network(
        spec, args.mode, out, args.rate, duration=args.duration, server_bin=args.server_bin
    )
    print(json.dumps(summary))

    if args.analyze:
        sys.path.insert(0, str(_OBJ_A / "mockups"))
        import analyze_run

        analyze_run.analyze(out, analyze_run.load_specs(out / "queues.csv"), trace_limit=40)
    return 1 if summary["unaccounted"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
