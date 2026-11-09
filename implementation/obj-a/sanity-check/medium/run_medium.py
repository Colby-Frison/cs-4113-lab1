#!/usr/bin/env python3
"""Medium Objective A sanity check: 100 independent wait-job queues.

This is not a domain model. Each queue is the Lab 1 sanity system (a file of
wait tasks, one queue process, one stateless server function, one sink),
copied until the medium band is full. Queues do not talk to each other.

What one invocation does, in order
----------------------------------
1. Parse CLI flags (scenario filter, queue count, jobs per queue, wait).
2. For each of the seven server-factor scenarios (or ``--only`` subset):
   a. Wipe and recreate ``runs/<scenario>/``.
   b. Scale the baseline of 4 servers per queue by that scenario's factor.
   c. Generate one CSV of wait jobs and split it round-robin across queues.
   d. Start every server function, wait until each log shows ``gRPC listen``.
   e. Start every queue process so they read their CSV and submit jobs.
   f. Poll the sinks until every job is delivered, then kill the processes.
   g. Run ``analyze_run.py`` on the merged logs.
   h. Copy tool artifacts and write the six rubric plots under ``results/``.
3. Write ``results/summary.md`` and ``results/sensitivity-latency.png``.

Defaults, from the lab slides for MEDIUM
----------------------------------------
- 100 queues (tasksheet band 10–100; the slide says "100s")
- 10,000 jobs total (100 per queue; low end of 10,000–100,000)
- baseline 4 servers per queue, then the seven server factors

Queue depth stays at the burst size so every job is admitted. The server
count is the resource the seven sets change. A one-server baseline cannot
shrink under the "at least one server" rule, which is why the baseline is 4.

Run from the repo root::

    python implementation/obj-a/sanity-check/medium/run_medium.py
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

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# medium/run_medium.py -> medium -> sanity-check -> obj-a -> implementation -> repo
REPO = Path(__file__).resolve().parents[4]
OBJ_A = REPO / "implementation" / "obj-a"
HERE = Path(__file__).resolve().parent

QUEUE_PY = OBJ_A / "queue" / "queue.py"
SF_PY = OBJ_A / "server-func-stateless" / "server_func.py"
GENERATOR_PY = OBJ_A / "workload-generator" / "workload_generator.py"
ANALYZE = OBJ_A / "mockups" / "analyze_run.py"

# ---------------------------------------------------------------------------
# Study defaults (override with CLI flags)
# ---------------------------------------------------------------------------
QUEUE_COUNT = 100
JOBS_PER_QUEUE = 100
WAIT_SEC = 0.05
BASE_SERVERS = 4
# Each queue's server function listens on 127.0.0.1:(SF_PORT0 + queue_index).
SF_PORT0 = 26000
LISTEN_TIMEOUT_SEC = 120
# Jobs shown in the accessory etrace.png from analyze_run.py.
TRACE_LIMIT = 40

# Seven server-factor sets required by the scale-study rubric.
# Name is the results/ folder; factor multiplies the baseline server total.
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
    """Scale per-queue server counts by ``factor``, preserving integer totals.

    Steps:
    1. Multiply each baseline count by ``factor``.
    2. Floor each value, but never below 1 (a queue always has a server).
    3. Compute a target total of ``round(sum(baseline) * factor)``, at least
       one server per queue.
    4. Nudge individual queues up or down so the floored map hits that total.
       When adding, prefer queues with the largest fractional remainder.
       When removing, prefer queues that were rounded up the most, but never
       drop a queue below 1.

    Factor 1.0 returns a copy of ``baseline`` unchanged.
    """
    if factor == 1.0:
        return dict(baseline)
    raw = {index: count * factor for index, count in baseline.items()}
    out = {index: max(1, math.floor(value)) for index, value in raw.items()}
    target = max(len(baseline), round(sum(baseline.values()) * factor))
    diff = target - sum(out.values())
    if diff > 0:
        # Give the leftover servers to the queues closest to the next integer.
        order = sorted(raw, key=lambda index: raw[index] - math.floor(raw[index]), reverse=True)
        for index in order[:diff]:
            out[index] += 1
    elif diff < 0:
        # Take servers back from queues that were floored furthest above the raw value.
        order = sorted(
            (index for index in out if out[index] > 1),
            key=lambda index: raw[index] - out[index],
        )
        for index in order[: -diff]:
            out[index] -= 1
    return out


def spawn(args: list[str], err_path: Path) -> subprocess.Popen:
    """Start a child process with stdout discarded and stderr written to a file.

    Working directory is the repo root so relative imports inside Obj A scripts
    resolve the same way as a manual launch. On Windows, ``CREATE_NO_WINDOW``
    keeps a console from flashing for each of the hundreds of children.
    """
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
    """Kill any still-running children, then wait briefly for them to exit."""
    for process in processes:
        if process.poll() is None:
            process.kill()
    for process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def wait_for_listen(logs: list[Path], timeout: float) -> None:
    """Block until every server-function log contains ``gRPC listen``.

    Queues must not start before their destination port is bound. Polling the
    log text matches the framework's existing readiness signal. Raises if any
    listener is still missing when ``timeout`` expires.
    """
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
    """Build one seeded job stream, then split it round-robin across queues.

    A single generator call keeps job ids unique across all sources
    (``j0`` … ``j{total-1}``). Arrival delay is fixed at 0 so every job is
    ready immediately: a burst, not a slow Poisson arrival. The wait task is
    a fixed sleep so service time is dominated by ``wait_sec``, not RPC.

    Writes ``jobs-all.csv`` plus ``jobs-q0.csv`` … ``jobs-q{N-1}.csv`` under
    ``run_dir``. Returns the total job count.
    """
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
    with combined.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    header, jobs = rows[0], [row for row in rows[1:] if row]
    if len(jobs) != total:
        raise RuntimeError(f"generator wrote {len(jobs)} jobs, expected {total}")
    # Round-robin keeps each queue's share equal and preserves global id order.
    buckets: list[list[list[str]]] = [[] for _ in range(queues)]
    for index, row in enumerate(jobs):
        buckets[index % queues].append(row)
    for index, bucket in enumerate(buckets):
        with (run_dir / f"jobs-q{index}.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(header)
            writer.writerows(bucket)
    return total


def sink_rows(run_dir: Path) -> int:
    """Count non-empty lines across every ``sink-q*.csv`` in ``run_dir``."""
    total = 0
    for path in run_dir.glob("sink-q*.csv"):
        total += sum(1 for line in path.read_text(encoding="utf-8", errors="ignore").splitlines() if line.strip())
    return total


def start_servers(queues: int, servers: dict[int, int], run_dir: Path) -> tuple[list[subprocess.Popen], list[Path]]:
    """Launch one stateless server function per queue and wait for bind.

    ``--ingrpc host:port,workers`` sets both the listen address and the gRPC
    worker pool size (the "servers" resource under test). Completed jobs are
    appended to ``sink-q<id>.csv``.
    """
    processes: list[subprocess.Popen] = []
    logs: list[Path] = []
    for index in range(queues):
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
) -> list[subprocess.Popen]:
    """Launch one queue process per queue after its server is already listening.

    Each queue reads ``jobs-q<id>.csv``, buffers up to ``qsize`` jobs, and
    forwards admitted jobs over gRPC to the matching server-function port.
    The worker count in ``--outgrpc`` must match the server's ``--ingrpc``.
    """
    processes: list[subprocess.Popen] = []
    for index in range(queues):
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
    """Collect short stderr tails from up to six ``*.err`` files for diagnostics."""
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
) -> dict:
    """Execute one server-factor scenario end to end and return a result row.

    Layout under ``runs/<name>/``:
    - ``jobs-q*.csv`` / ``jobs-all.csv`` — workload
    - ``servers.csv`` — per-queue qsize and server count (feeds analysis)
    - ``q*.log`` / ``sf-q*.log`` — process event logs
    - ``sink-q*.csv`` — completed jobs
    - ``analysis/`` — written by ``analyze_run.py``

    Drain timeout scales with jobs per queue, wait time, and the slowest
    (smallest) server pool in the scenario so a reduced-server set still
    finishes instead of timing out early.
    """
    run_dir = runs / name
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    baseline = {index: BASE_SERVERS for index in range(queues)}
    servers = scale_servers(baseline, factor)
    # Depth equals the burst so QUEUE-FULL should not fire; servers are the knob.
    qsize = jobs_per_queue
    jobs = generate_jobs(run_dir, queues, jobs_per_queue, wait_sec)
    with (run_dir / "servers.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["qid", "qsize", "servers"])
        for index in range(queues):
            writer.writerow([f"q{index}", qsize, servers[index]])

    server_procs: list[subprocess.Popen] = []
    queue_procs: list[subprocess.Popen] = []
    try:
        server_procs, _logs = start_servers(queues, servers, run_dir)
        queue_procs = start_queues(queues, servers, qsize, run_dir)
        slowest = max(1, min(servers.values()))
        drain_for = 90 + (jobs_per_queue * wait_sec / slowest) * 5
        deadline = time.time() + drain_for
        while time.time() < deadline and sink_rows(run_dir) < jobs:
            time.sleep(0.4)
        # Brief settle so the last DEPART-VIA lines flush before we kill children.
        time.sleep(0.8)
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
    }


def _longest_jobs(events: Path, count: int) -> list[str]:
    """Return job ids with the largest source-arrival to sink-departure span.

    ``ARRIVE-VIA`` on a non-queue edge is the job entering from its CSV.
    ``DEPART-VIA sink-…`` is the job leaving the server function. The
    difference is end-to-end response for that job.
    """
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


# Accessory files that analyze_run.py produces; copied into results/<name>/tools/.
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
    """Copy analysis tool outputs into ``out/tools/`` for the checked-in tree."""
    tools = out / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    analysis = scenario["run_dir"] / "analysis"
    for name in TOOL_FILES:
        source = analysis / name
        if not source.is_file():
            raise FileNotFoundError(source)
        shutil.copy2(source, tools / name)


def write_six_plots(scenario: dict, results: Path) -> None:
    """Write the six rubric figures for one scenario under ``results/<name>/``.

    Per-queue USE series come from ``quse.csv``. ``q0`` is drawn darker; the
    other queues are faint overlays so the plot stays readable at 100 width.
    System latency and throughput come from ``sysmetrics.csv``. The longest
    three jobs are filtered and traced into ``longest-jobs.png``.

    Also fills scenario fields used by the summary table: drops, mean latency,
    busiest queue, and peak utilization / saturation.
    """
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
        """One USE-family time series with q0 highlighted."""
        figure, axis = plt.subplots(figsize=(8, 4.5))
        for qid, frame in quse.groupby("queue-sys-id"):
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
        axis.set_title(f"{title} {ylabel} ({scenario['queues']} queues)")
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
    # Throughput-weighted mean of average response time across time steps.
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
    """Write the comparison markdown table and the cross-scenario latency bar chart."""
    results.mkdir(parents=True, exist_ok=True)
    first = rows[0]
    lines = [
        "# Medium wait-job sanity check",
        "",
        f"{first['queues']} independent queue systems. Each job is one wait of {first['wait_sec']:.3f}s.",
        f"Each scenario offers {first['jobs']} jobs "
        f"({first['jobs'] // first['queues']} per queue), queue depth {first['qsize']}.",
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
    """CLI entry: run selected scenarios, plot each, then write the summary."""
    parser = argparse.ArgumentParser(
        description=(
            "Run the medium Objective A wait-job sanity check: N independent "
            "queues, a fixed-wait burst, and seven server-count factors."
        )
    )
    parser.add_argument("--only", default="", help="Comma-separated scenario names (default: all seven)")
    parser.add_argument("--queues", type=int, default=QUEUE_COUNT)
    parser.add_argument("--jobs-per-queue", type=int, default=JOBS_PER_QUEUE)
    parser.add_argument("--wait", type=float, default=WAIT_SEC)
    parser.add_argument("--runs", type=Path, default=HERE / "runs")
    parser.add_argument("--results", type=Path, default=HERE / "results")
    args = parser.parse_args(argv)
    if args.queues < 1 or args.jobs_per_queue < 1 or args.wait < 0:
        raise SystemExit("queues, jobs-per-queue, and wait must be positive")
    wanted = {name.strip() for name in args.only.split(",") if name.strip()}
    chosen = [item for item in SCENARIOS if not wanted or item[0] in wanted]
    if len(chosen) != (len(wanted) or len(SCENARIOS)):
        known = ", ".join(name for name, _ in SCENARIOS)
        raise SystemExit(f"unknown scenario in --only. Known: {known}")
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
    print(f"summary: {args.results / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
