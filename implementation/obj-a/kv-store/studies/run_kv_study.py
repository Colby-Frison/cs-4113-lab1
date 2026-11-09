#!/usr/bin/env python3
"""USE + Thr/Lat sweep over KV shard (N) and replica (M) configurations.

Each scenario is one (N, M) topology. Jobs are hash-partitioned across N
queues; when M >= 2 each shard coordinator runs 2PC against M replicas.
Analysis reuses ``mockups/analyze_run.py`` and writes the six rubric plots
plus a cross-config summary.
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

OBJ_A = Path(__file__).resolve().parents[2]
REPO = OBJ_A.parents[1]
HERE = Path(__file__).resolve().parent
if str(OBJ_A) not in sys.path:
    sys.path.insert(0, str(OBJ_A))
KV_STORE = OBJ_A / "kv-store"
if str(KV_STORE) not in sys.path:
    sys.path.insert(0, str(KV_STORE))

from launch import (  # noqa: E402
    count_sink_jobs,
    generate_jobs,
    start_topology,
    stop,
    wait_for_sinks,
)
from topology import build_topology, write_partitioned_jobs  # noqa: E402

ANALYZE = OBJ_A / "mockups" / "analyze_run.py"
RUNS = HERE / "runs"
RESULTS = HERE / "results"

# (name, shards N, replicas M)
CONFIGS: list[tuple[str, int, int]] = [
    ("n1-m1", 1, 1),
    ("n2-m1", 2, 1),
    ("n4-m1", 4, 1),
    ("n1-m2", 1, 2),
    ("n1-m4", 1, 4),
    ("n2-m2", 2, 2),
    ("n4-m2", 4, 2),
]

JOBS = 80
WAIT_SEC = 0.02
QSIZE = 200
WORKERS = 2
TRACE_LIMIT = 40
SF_PORT0 = 30000
REPLICA_PORT0 = 31000

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


def run_scenario(
    name: str,
    shards: int,
    replicas: int,
    *,
    jobs: int,
    wait: float,
    qsize: int,
    workers: int,
    port_offset: int,
) -> dict:
    run_dir = RUNS / name
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    topology = build_topology(
        run_dir,
        shards=shards,
        replicas=replicas,
        sf_port0=SF_PORT0 + port_offset,
        replica_port0=REPLICA_PORT0 + port_offset,
    )
    all_jobs = run_dir / "jobs-all.csv"
    generate_jobs(all_jobs, count=jobs, wait=wait)
    write_partitioned_jobs(all_jobs, topology)
    with (run_dir / "servers.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["qid", "qsize", "servers"])
        for plan in topology.plans:
            writer.writerow([f"q{plan.shard}", qsize, workers])

    children = start_topology(topology, qsize=qsize, workers=workers, listen_timeout_sec=45.0)
    try:
        wait_for_sinks(topology, jobs, timeout_sec=max(60.0, jobs * wait * 6 + 30))
        # Let coordinators flush a final store-kv-to after the last visit.
        time.sleep(0.2)
    finally:
        stop(children)

    delivered = count_sink_jobs(topology)
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
    )
    if analysis.returncode != 0:
        raise RuntimeError(f"analyze_run failed for {name} with code {analysis.returncode}")

    return {
        "name": name,
        "shards": shards,
        "replicas": replicas,
        "jobs": jobs,
        "wait_sec": wait,
        "qsize": qsize,
        "workers": workers,
        "delivered": delivered,
        "run_dir": run_dir,
    }


def publish_tools(scenario: dict, out: Path) -> None:
    tools = out / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    analysis = scenario["run_dir"] / "analysis"
    for name in TOOL_FILES:
        source = analysis / name
        if source.is_file():
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
        for qid, frame in quse.groupby("queue-sys-id"):
            highlight = str(qid) in ("0", "q0") or int(qid) == 0
            axis.plot(
                frame["time-step"],
                frame[column],
                color="#1f4e79" if highlight else "#9bb7d4",
                linewidth=1.5 if highlight else 0.7,
                alpha=1.0 if highlight else 0.45,
                label="q0" if highlight else None,
                zorder=2 if highlight else 1,
            )
        axis.set_xlabel("milliseconds from first event")
        axis.set_ylabel(ylabel)
        axis.set_title(f"{title} {ylabel} (N={scenario['shards']} M={scenario['replicas']})")
        if any(axis.get_legend_handles_labels()[0]):
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

    # Longest-jobs panel: reuse create-event-trace on the longest three ids.
    emetrics = analysis / "emetrics.csv"
    longest: list[str] = []
    if emetrics.is_file():
        frame = pd.read_csv(emetrics)
        job_col = "Job-id" if "Job-id" in frame.columns else "job-id"
        if job_col in frame.columns and "turnaround-time" in frame.columns:
            ordered = frame.sort_values("turnaround-time", ascending=False)
            # One row per visit; keep the three worst end-to-end job ids.
            longest = []
            for job in ordered[job_col].tolist():
                text = str(job)
                if text not in longest:
                    longest.append(text)
                if len(longest) == 3:
                    break
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
    if not quse.empty:
        scenario["peak_util"] = float(quse["Utilization(10)"].max())
        scenario["peak_sat"] = float(quse["Saturation(10)"].max())
    else:
        scenario["peak_util"] = float("nan")
        scenario["peak_sat"] = float("nan")


def write_summary(rows: list[dict], results: Path) -> None:
    results.mkdir(parents=True, exist_ok=True)
    lines = [
        "# KV store N x M study",
        "",
        "Stateful visit-count KV under hash sharding ($N$) and optional 2PC replication ($M$).",
        f"Each scenario offers {rows[0]['jobs']} jobs with fixed wait "
        f"{rows[0]['wait_sec']:.3f}s, queue depth {rows[0]['qsize']}, "
        f"{rows[0]['workers']} gRPC workers per process.",
        "Jobs are partitioned by `crc32(job_id) % N`. When $M \\ge 2$, each shard",
        "coordinator runs Prepare/Commit against $M$ replicas before updating its map.",
        "",
        "| scenario | N | M | delivered | dropped | mean latency (ms) | peak utilization | peak saturation |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        latency = row["mean_latency"]
        latency_text = f"{latency:.1f}" if not math.isnan(latency) else "nan"
        lines.append(
            f"| {row['name']} | {row['shards']} | {row['replicas']} | {row['delivered']} | "
            f"{row['drops']} | {latency_text} | {row['peak_util']:.3f} | {row['peak_sat']:.1f} |"
        )
    lines.extend(
        [
            "",
            "## How to read the plots",
            "",
            "- **Utilization / saturation / errors** are per shard queue (`q0` highlighted).",
            "- **Latency / throughput** are end-to-end across the system.",
            "- Raising $N$ parallelizes independent shard queues (more capacity for the same job count).",
            "- Raising $M$ adds 2PC round-trips on every visit increment, which usually increases",
            "  visit latency even when delivery succeeds.",
            "",
            "Six plots per scenario live in `results/<scenario>/`. Accessory tool output is under",
            "`results/<scenario>/tools/`. Raw `runs/` are gitignored.",
            "",
        ]
    )
    (results / "summary.md").write_text("\n".join(lines), encoding="utf-8")

    figure, axes = plt.subplots(1, 2, figsize=(10, 4.2))
    labels = [row["name"] for row in rows]
    axes[0].bar(labels, [row["mean_latency"] for row in rows], color="#2a78d6")
    axes[0].set_ylabel("mean end-to-end latency (ms)")
    axes[0].set_title("Latency vs N,M")
    axes[0].tick_params(axis="x", rotation=45)
    delivered = [row["delivered"] for row in rows]
    axes[1].bar(labels, delivered, color="#2a9d4a")
    axes[1].set_ylabel("jobs delivered")
    axes[1].set_title("Delivery vs N,M")
    axes[1].tick_params(axis="x", rotation=45)
    figure.tight_layout()
    figure.savefig(results / "sensitivity-nm.png", dpi=120)
    plt.close(figure)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KV N x M USE+Thr/Lat study")
    parser.add_argument("--jobs", type=int, default=JOBS)
    parser.add_argument("--wait", type=float, default=WAIT_SEC)
    parser.add_argument("--only", help="Comma-separated scenario names (default: all)")
    parser.add_argument("--results", type=Path, default=RESULTS)
    args = parser.parse_args(argv)
    selected = {name.strip() for name in args.only.split(",")} if args.only else None
    configs = [item for item in CONFIGS if selected is None or item[0] in selected]
    if not configs:
        raise SystemExit("no scenarios selected")

    rows: list[dict] = []
    for index, (name, shards, replicas) in enumerate(configs):
        scenario = run_scenario(
            name,
            shards,
            replicas,
            jobs=args.jobs,
            wait=args.wait,
            qsize=QSIZE,
            workers=WORKERS,
            port_offset=index * 40,
        )
        write_six_plots(scenario, args.results)
        rows.append(scenario)
    write_summary(rows, args.results)
    print(f"wrote {args.results / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
