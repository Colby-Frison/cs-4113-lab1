#!/usr/bin/env python3
"""Medium Objective A sanity check: 100 independent wait-job queues.

This is not a domain model. Each queue is the Lab 1 sanity system (a file of
wait tasks, one queue, one stateless server function, one sink), copied until
the medium band is full.

Defaults, from the lab slides for MEDIUM:

- 100 queues (the tasksheet band is 10–100; the slide says "100s")
- 10,000 jobs (the low end of 10,000–100,000)
- baseline 4 servers per queue, then the seven server factors

Queue depth stays at the burst size so every job is admitted. The server
count is the resource the seven sets change. A one-server baseline cannot
shrink under the "at least one server" rule, which is why the baseline is 4.
"""

from __future__ import annotations

import argparse
import csv
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

REPO = Path(__file__).resolve().parents[3]
OBJ_A = REPO / "implementation" / "obj-a"
HERE = Path(__file__).resolve().parent

QUEUE_PY = OBJ_A / "queue" / "queue.py"
SF_PY = OBJ_A / "server-func-stateless" / "server_func.py"
GENERATOR_PY = OBJ_A / "workload-generator" / "workload_generator.py"
ANALYZE = OBJ_A / "mockups" / "analyze_run.py"

QUEUE_COUNT = 100
JOBS_PER_QUEUE = 100
WAIT_SEC = 0.05
BASE_SERVERS = 4
SF_PORT0 = 26000
LISTEN_TIMEOUT_SEC = 120
TRACE_LIMIT = 40

SCENARIOS: list[tuple[str, float]] = [
    ("base", 1.0),
    ("srv-10", 0.9),
    ("srv-20", 0.8),
    ("srv-30", 0.7),
    ("srv+25", 1.25),
    ("srv+50", 1.5),
    ("srv+100", 2.0),
]


def scale_servers(baseline: dict[int, int], factor: float) -> dict[int, int]:
    """Floor each count, keep at least one, then nudge the total to ``factor`` times baseline."""
    if factor == 1.0:
        return dict(baseline)
    raw = {index: count * factor for index, count in baseline.items()}
    out = {index: max(1, math.floor(value)) for index, value in raw.items()}
    target = max(len(baseline), round(sum(baseline.values()) * factor))
    diff = target - sum(out.values())
    if diff > 0:
        order = sorted(raw, key=lambda index: raw[index] - math.floor(raw[index]), reverse=True)
        for index in order[:diff]:
            out[index] += 1
    elif diff < 0:
        order = sorted(
            (index for index in out if out[index] > 1),
            key=lambda index: raw[index] - out[index],
        )
        for index in order[: -diff]:
            out[index] -= 1
    return out


def spawn(args: list[str], err_path: Path) -> subprocess.Popen:
    err_path.parent.mkdir(parents=True, exist_ok=True)
    handle = err_path.open("w", encoding="utf-8")
    try:
        return subprocess.Popen(
            args,
            cwd=str(REPO),
            stdout=subprocess.DEVNULL,
            stderr=handle,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    finally:
        handle.close()


def stop(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is None:
            process.kill()
    for process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def wait_for_listen(logs: list[Path], timeout: float) -> None:
    deadline = time.time() + timeout
    pending = set(logs)
    while pending and time.time() < deadline:
        ready = [
            path
            for path in pending
            if path.exists() and "gRPC listen" in path.read_text(encoding="utf-8", errors="ignore")
        ]
        for path in ready:
            pending.discard(path)
        if pending:
            time.sleep(0.25)
    if pending:
        names = ", ".join(path.name for path in list(pending)[:8])
        raise RuntimeError(f"{len(pending)} listeners did not bind ({names})")


def generate_jobs(run_dir: Path, queues: int, jobs_per_queue: int, wait_sec: float) -> int:
    """One generator stream, then a round-robin split so job ids stay unique."""
    total = queues * jobs_per_queue
    combined = run_dir / "jobs-all.csv"
    generated = subprocess.run(
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
            str(wait_sec),
            "--tasks",
            "1",
            "--count",
            str(total),
            "--seed",
            "1",
            "--out",
            str(combined),
        ],
        cwd=str(REPO),
        check=False,
        capture_output=True,
        text=True,
    )
    if generated.returncode != 0:
        raise RuntimeError(f"workload generator failed\n{generated.stderr[-2000:]}")
    # Keep file descriptor use bounded, including for 1024 queues.
    from collections import OrderedDict
    from contextlib import ExitStack
    with combined.open(encoding="utf-8", newline="") as incoming:
        reader = csv.reader(incoming)
        header = next(reader)
        for index in range(queues):
            with (run_dir / f"jobs-q{index}.csv").open("w", encoding="utf-8", newline="") as handle:
                csv.writer(handle, lineterminator="\n").writerow(header)
        handles = OrderedDict()
        try:
            count = 0
            for row in reader:
                if not row:
                    continue
                index = count % queues
                if index not in handles:
                    if len(handles) >= 64:
                        _, old = handles.popitem(last=False)
                        old.close()
                    handles[index] = (run_dir / f"jobs-q{index}.csv").open(
                        "a", encoding="utf-8", newline=""
                    )
                handles.move_to_end(index)
                csv.writer(handles[index], lineterminator="\n").writerow(row)
                count += 1
            if count != total:
                raise RuntimeError(f"generator wrote {count} jobs, expected {total}")
        finally:
            for handle in handles.values():
                handle.close()
    combined.unlink()
    return total


def sink_rows(run_dir: Path, start: int = 0, end: int | None = None) -> int:
    total = 0
    paths = (run_dir / f"sink-q{i}.csv" for i in range(start, end)) if end is not None else run_dir.glob("sink-q*.csv")
    for path in paths:
        if path.exists():
            with path.open(encoding="utf-8", errors="ignore") as handle:
                total += sum(bool(line.strip()) for line in handle)
    return total


def start_servers(queues: int, servers: dict[int, int], run_dir: Path, start: int = 0) -> tuple[list[subprocess.Popen], list[Path]]:
    processes: list[subprocess.Popen] = []
    logs: list[Path] = []
    for index in range(start, start + queues):
        qid = f"q{index}"
        log_path = run_dir / f"sf-{qid}.log"
        args = [
            sys.executable,
            str(SF_PY),
            "--qid",
            qid,
            "--server-id",
            "0",
            "--ingrpc",
            f"127.0.0.1:{SF_PORT0 + index},{servers[index]}",
            "--logfile",
            str(log_path),
            "-o",
            str(run_dir / f"sink-{qid}.csv"),
        ]
        processes.append(spawn(args, run_dir / f"sf-{qid}.err"))
        logs.append(log_path)
    wait_for_listen(logs, LISTEN_TIMEOUT_SEC)
    return processes, logs


def start_queues(
    queues: int,
    servers: dict[int, int],
    qsize: int,
    run_dir: Path,
    start: int = 0,
) -> list[subprocess.Popen]:
    processes: list[subprocess.Popen] = []
    for index in range(start, start + queues):
        qid = f"q{index}"
        args = [
            sys.executable,
            str(QUEUE_PY),
            "--qid",
            qid,
            "--qsize",
            str(qsize),
            "-i",
            str(run_dir / f"jobs-{qid}.csv"),
            "--outgrpc",
            f"127.0.0.1:{SF_PORT0 + index},{servers[index]}",
            "--logfile",
            str(run_dir / f"{qid}.log"),
        ]
        processes.append(spawn(args, run_dir / f"{qid}.err"))
    return processes


def err_tail(run_dir: Path) -> str:
    chunks: list[str] = []
    for path in sorted(run_dir.glob("*.err")):
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        if text:
            chunks.append(f"--- {path.name}\n{text[-400:]}")
        if len(chunks) == 6:
            break
    return "\n".join(chunks)


def run_scenario(
    name: str,
    factor: float,
    runs: Path,
    queues: int,
    jobs_per_queue: int,
    wait_sec: float,
    batch_size: int = 0,
    backend: str = "grpc",
) -> dict:
    run_dir = runs / name
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    baseline = {index: BASE_SERVERS for index in range(queues)}
    servers = scale_servers(baseline, factor)
    qsize = jobs_per_queue
    jobs = generate_jobs(run_dir, queues, jobs_per_queue, wait_sec)
    with (run_dir / "servers.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["qid", "qsize", "servers"])
        for index in range(queues):
            writer.writerow([f"q{index}", qsize, servers[index]])

    if backend == "grpc-shared":
        from grpc_shared import run_grpc_shared
        run_grpc_shared(run_dir, servers, qsize, jobs_per_queue, wait_sec)
    elif backend == "shared":
        from shared_queues import run_shared_queues
        run_shared_queues(run_dir, servers, qsize, jobs_per_queue, wait_sec)
    else:
        # Existing gRPC/process mode is retained unchanged.
        # batch_size=0 means the original, fully simultaneous experiment.
        # A positive batch_size caps live process pairs, but does NOT measure
        # globally simultaneous queues; results must be labeled accordingly.
        step = batch_size or queues
        for start in range(0, queues, step):
            end = min(start + step, queues)
            server_procs: list[subprocess.Popen] = []
            queue_procs: list[subprocess.Popen] = []
            try:
                server_procs, _logs = start_servers(end - start, servers, run_dir, start)
                queue_procs = start_queues(end - start, servers, qsize, run_dir, start)
                slowest = max(1, min(servers[i] for i in range(start, end)))
                drain_for = 90 + (jobs_per_queue * wait_sec / slowest) * 5
                deadline = time.time() + drain_for
                expected = (end - start) * jobs_per_queue
                while time.time() < deadline and sink_rows(run_dir, start, end) < expected:
                    if all(proc.poll() is not None for proc in queue_procs):
                        # Workers finished; allow any in-flight server requests to drain.
                        pass
                    time.sleep(0.4)
                time.sleep(0.8)
                actual = sink_rows(run_dir, start, end)
                print(f"  batch queues {start}-{end - 1}: {actual}/{expected} delivered", flush=True)
                if actual < expected:
                    raise RuntimeError(f"{name}: batch {start}-{end - 1} delivered {actual}/{expected}\n{err_tail(run_dir)}")
            finally:
                stop(queue_procs + server_procs)

    delivered = sink_rows(run_dir)
    if delivered < jobs:
        raise RuntimeError(
            f"{name}: delivered {delivered}/{jobs}\n{err_tail(run_dir)}"
        )

    analysis = subprocess.run(
        [
            sys.executable,
            str(ANALYZE),
            str(run_dir),
            "--qconf-file",
            str(run_dir / "servers.csv"),
            "--trace-limit",
            str(TRACE_LIMIT),
        ],
        cwd=str(REPO),
        check=False,
        capture_output=True,
        text=True,
    )
    if analysis.returncode != 0:
        (run_dir / "analyze.err").write_text(analysis.stderr, encoding="utf-8")
        raise RuntimeError(f"{name}: analysis failed\n{analysis.stderr[-2000:]}")
    return {
        "name": name,
        "factor": factor,
        "queues": queues,
        "servers": sum(servers.values()),
        "server_min": min(servers.values()),
        "server_max": max(servers.values()),
        "qsize": qsize,
        "jobs": jobs,
        "wait_sec": wait_sec,
        "delivered": delivered,
        "run_dir": run_dir,
        "batch_size": batch_size,
        "backend": backend,
    }


def _longest_jobs(events: Path, count: int) -> list[str]:
    arrivals: dict[str, int] = {}
    departures: dict[str, int] = {}
    for line in events.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        kind = parts[4]
        if kind == "ARRIVE-VIA" and parts[1] not in arrivals and not parts[5].startswith("q"):
            arrivals[parts[1]] = int(parts[0])
        elif kind == "DEPART-VIA" and parts[5].startswith("sink-"):
            departures[parts[1]] = int(parts[0])
    spans = [
        (departures[job] - arrivals[job], job)
        for job in arrivals
        if job in departures
    ]
    spans.sort(reverse=True)
    return [job for _, job in spans[:count]]


TOOL_FILES = (
    "events.log",
    "qmet.csv",
    "qconf.csv",
    "q0-qmet.csv",
    "quse.csv",
    "use.png",
    "emetrics.csv",
    "sysmetrics.csv",
    "resplot.png",
    "response-hist.png",
    "response-scatter.png",
    "etrace.png",
    "pp-events.log",
    "pp-longest.log",
    "longest-job.log",
)


def publish_tools(scenario: dict, out: Path) -> None:
    tools = out / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    analysis = scenario["run_dir"] / "analysis"
    for name in TOOL_FILES:
        source = analysis / name
        if not source.is_file():
            raise FileNotFoundError(source)
        shutil.copy2(source, tools / name)


def write_six_plots(scenario: dict, results: Path) -> None:
    analysis = scenario["run_dir"] / "analysis"
    out = results / scenario["name"]
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    publish_tools(scenario, out)
    title = scenario["name"]
    quse = pd.read_csv(analysis / "quse.csv")
    sysmetrics = pd.read_csv(analysis / "sysmetrics.csv")

    def panel(column: str, ylabel: str, filename: str) -> None:
        figure, axis = plt.subplots(figsize=(8, 4.5))
        # Plot a representative subset at very large queue counts to keep
        # Matplotlib from spending minutes rendering thousands of line objects.
        # All queues are still included in the simulation and metric calculations.
        plotted_ids = {0}
        if scenario["queues"] <= 64:
            plotted_ids.update(range(scenario["queues"]))
        else:
            plotted_ids.update(round(i * (scenario["queues"] - 1) / 31) for i in range(32))
        for qid, frame in quse.groupby("queue-sys-id"):
            if int(qid) not in plotted_ids:
                continue
            highlight = int(qid) == 0
            axis.plot(
                frame["time-step"],
                frame[column],
                color="#1f4e79" if highlight else "#9bb7d4",
                linewidth=1.5 if highlight else 0.7,
                alpha=1.0 if highlight else 0.35,
                label="q0" if highlight else None,
                zorder=2 if highlight else 1,
            )
        axis.set_xlabel("milliseconds from first event")
        axis.set_ylabel(ylabel)
        note = f"; {len(plotted_ids)} shown" if scenario["queues"] > 64 else ""
        axis.set_title(f"{title} {ylabel} ({scenario['queues']} queues{note})")
        axis.legend(frameon=False)
        figure.tight_layout()
        figure.savefig(out / filename, dpi=120)
        plt.close(figure)

    panel("Utilization(10)", "utilization", "utilization.png")
    panel("Saturation(10)", "saturation", "saturation.png")
    panel("Errors(10)", "errors", "errors.png")

    figure, axis = plt.subplots(figsize=(8, 4.5))
    if not sysmetrics.empty:
        axis.plot(sysmetrics["time-step"], sysmetrics["median-response-time"], label="median")
        axis.plot(sysmetrics["time-step"], sysmetrics["Avg-response-time"], label="average")
        axis.fill_between(
            sysmetrics["time-step"],
            sysmetrics["10-response-time"],
            sysmetrics["90-response-time"],
            alpha=0.2,
            label="P10–P90",
        )
    axis.set_xlabel("milliseconds from first event")
    axis.set_ylabel("end-to-end response (ms)")
    axis.set_title(f"{title} latency")
    axis.legend(frameon=False)
    figure.tight_layout()
    figure.savefig(out / "latency.png", dpi=120)
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(8, 4.5))
    if not sysmetrics.empty:
        axis.plot(sysmetrics["time-step"], sysmetrics["throughput"], color="#2a9d4a")
    axis.set_xlabel("milliseconds from first event")
    axis.set_ylabel("jobs leaving per millisecond")
    axis.set_title(f"{title} throughput")
    figure.tight_layout()
    figure.savefig(out / "throughput.png", dpi=120)
    plt.close(figure)

    longest = _longest_jobs(analysis / "events.log", 3)
    if longest:
        filtered = out / "longest-jobs.log"
        subprocess.run(
            [
                sys.executable,
                str(OBJ_A / "filter-by-job" / "filter_by_job.py"),
                "--events",
                str(analysis / "events.log"),
                "--jobs",
                ",".join(longest),
                "--out",
                str(filtered),
            ],
            check=True,
        )
        subprocess.run(
            [
                sys.executable,
                str(OBJ_A / "create-event-trace" / "create_event_trace.py"),
                "--events",
                str(filtered),
                "--out",
                str(out / "longest-jobs.png"),
            ],
            check=True,
        )
    scenario["longest"] = longest
    scenario["drops"] = sum(
        1
        for line in (analysis / "events.log").read_text(encoding="utf-8").splitlines()
        if "QUEUE-FULL" in line
    )
    valid = sysmetrics.dropna(subset=["Avg-response-time"]) if not sysmetrics.empty else sysmetrics
    weights = valid["throughput"] if not valid.empty else None
    if weights is not None and float(weights.sum()):
        scenario["mean_latency"] = float((valid["Avg-response-time"] * weights).sum() / weights.sum())
    else:
        scenario["mean_latency"] = float("nan")
    peaks = quse.groupby("queue-sys-id")["Utilization(10)"].max().sort_values(ascending=False)
    scenario["busiest"] = f"q{peaks.index[0]}" if not peaks.empty else ""
    scenario["peak_util"] = float(quse["Utilization(10)"].max()) if not quse.empty else float("nan")
    scenario["peak_sat"] = float(quse["Saturation(10)"].max()) if not quse.empty else float("nan")


def write_summary(rows: list[dict], results: Path) -> None:
    results.mkdir(parents=True, exist_ok=True)
    first = rows[0]
    lines = [
        "# Medium wait-job sanity check",
        "",
        f"{first['queues']} independent queue systems. Each job is one wait of {first['wait_sec']:.3f}s.",
        (f"WARNING: sequential batches of {first['batch_size']} queues. These are NOT {first['queues']} simultaneous queues; throughput, timing and saturation are not equivalent to the full concurrent run." if first["batch_size"] else "All queues run simultaneously."),
        f"Each scenario offers {first['jobs']} jobs "
        f"({first['jobs'] // first['queues']} per queue), queue depth {first['qsize']}.",
        ("Backend: shared asyncio (logical queues and service slots; no gRPC transport overhead)." if first.get("backend") == "shared" else ("Backend: shared-process gRPC." if first.get("backend") == "grpc-shared" else "Backend: original gRPC subprocesses.")),
        "Servers are the resource under test. Time in the plots is milliseconds from the first event.",
        "",
        "| scenario | server factor | servers | per queue | delivered | dropped | mean latency (ms) | peak utilization | peak saturation |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        per_queue = f"{row['server_min']}" if row["server_min"] == row["server_max"] else f"{row['server_min']}–{row['server_max']}"
        lines.append(
            f"| {row['name']} | {row['factor']:.2f} | {row['servers']} | {per_queue} | {row['delivered']} | "
            f"{row['drops']} | {row['mean_latency']:.1f} | {row['peak_util']:.3f} | {row['peak_sat']:.1f} |"
        )
    lines.extend(
        [
            "",
            "Six plots per scenario, in `results/<scenario>/`:",
            "",
            "- `utilization.png`, `saturation.png`, `errors.png`",
            "- `latency.png`, `throughput.png`",
            "- `longest-jobs.png` (three longest jobs)",
            "",
            "Each `results/<scenario>/tools/` directory holds the accessory figures",
            "(`use.png`, `resplot.png`, the response plots, `etrace.png`).",
            "Logs and CSVs are written there on a re-run and are gitignored.",
            f"`etrace.png` is the first {TRACE_LIMIT} jobs. `longest-jobs.png` is the long-job trace the rubric asks for.",
            "",
        ]
    )
    (results / "summary.md").write_text("\n".join(lines), encoding="utf-8")

    figure, axis = plt.subplots(figsize=(8, 4.5))
    labels = [row["name"] for row in rows]
    axis.bar(labels, [row["mean_latency"] for row in rows], color="#2a78d6")
    axis.set_ylabel("mean end-to-end latency (ms)")
    axis.set_title("Server scaling, wait-job sanity check")
    figure.tight_layout()
    figure.savefig(results / "sensitivity-latency.png", dpi=120)
    plt.close(figure)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the medium wait-job sanity check.")
    parser.add_argument("--backend", choices=("grpc", "grpc-shared", "shared"), default="grpc", help="grpc-shared: concurrent logical queues using real gRPC in a shared process; grpc: original separate subprocesses; shared: no-gRPC asyncio.")
    parser.add_argument("--batch-size", type=int, default=0, help="Run at most N queues simultaneously (0=original full concurrency). Batching changes experiment semantics.")
    parser.add_argument("--png-only", action="store_true", help="Delete raw run directories and ancillary tool outputs after all graphs and summary succeed.")
    parser.add_argument("--only", default="", help="Comma-separated scenario names (default: all seven)")
    parser.add_argument("--queues", type=int, default=QUEUE_COUNT)
    parser.add_argument("--jobs-per-queue", type=int, default=JOBS_PER_QUEUE)
    parser.add_argument("--wait", type=float, default=WAIT_SEC)
    parser.add_argument("--runs", type=Path, default=HERE / "runs")
    parser.add_argument("--results", type=Path, default=HERE / "results")
    args = parser.parse_args(argv)
    if args.queues < 1 or args.jobs_per_queue < 1 or args.wait < 0 or args.batch_size < 0:
        raise SystemExit("queues, jobs-per-queue, and wait must be positive")
    wanted = {name.strip() for name in args.only.split(",") if name.strip()}
    chosen = [item for item in SCENARIOS if not wanted or item[0] in wanted]
    if len(chosen) != (len(wanted) or len(SCENARIOS)):
        known = ", ".join(name for name, _ in SCENARIOS)
        raise SystemExit(f"unknown scenario in --only. Known: {known}")
    if args.backend in ("shared", "grpc-shared") and args.batch_size:
        parser.error("--batch-size is only supported with --backend grpc")
    rows = []
    for name, factor in chosen:
        print(
            f"scenario {name} factor {factor} queues {args.queues} jobs {args.queues * args.jobs_per_queue}",
            flush=True,
        )
        started = time.time()
        scenario = run_scenario(
            name,
            factor,
            args.runs,
            args.queues,
            args.jobs_per_queue,
            args.wait,
            args.batch_size,
            args.backend,
        )
        write_six_plots(scenario, args.results)
        print(
            f"  delivered {scenario['delivered']}/{scenario['jobs']} "
            f"servers {scenario['servers']} drops {scenario['drops']} "
            f"latency {scenario['mean_latency']:.1f} ms "
            f"({time.time() - started:.0f}s)",
            flush=True,
        )
        rows.append(scenario)
    write_summary(rows, args.results)
    if args.png_only:
        for item in rows:
            shutil.rmtree(item["run_dir"])
            tools_dir = args.results / item["name"] / "tools"
            if tools_dir.exists():
                shutil.rmtree(tools_dir)
            longest_log = args.results / item["name"] / "longest-jobs.log"
            if longest_log.exists():
                longest_log.unlink()
        print("Removed raw scenario files; kept PNG plots and summary.")
    print(f"summary: {args.results / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
