"""Six figures and the cross-scenario sensitivity charts for an expanded study.

Utilization, saturation, and errors draw one line per stage. Search is the
busiest search queue, not one line per shard.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from .arrival import expected_search_mean, expected_search_visits, ready_rho
from .expand import Layout

OBJ_A = Path(__file__).resolve().parents[2] / "obj-a"
TRACE_LIMIT = 40
CORE_TOOLS = (
    "events.log",
    "qmet.csv",
    "qconf.csv",
    "quse.csv",
    "use.png",
    "emetrics.csv",
    "sysmetrics.csv",
    "resplot.png",
    "response-hist.png",
    "response-scatter.png",
    "etrace.png",
    "pp-events.log",
)


def publish_tools(scenario: dict, out: Path) -> None:
    tools = out / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    analysis = scenario["run_dir"] / "analysis"
    for name in CORE_TOOLS:
        source = analysis / name
        if not source.is_file():
            raise FileNotFoundError(source)
        shutil.copy2(source, tools / name)
    for source in analysis.glob("q*-qmet.csv"):
        shutil.copy2(source, tools / source.name)
    for name in ("pp-longest.log", "longest-job.log"):
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
    stage_of: dict[int, str] = scenario["stage_of"]
    quse = pd.read_csv(analysis / "quse.csv")
    sysmetrics = pd.read_csv(analysis / "sysmetrics.csv")
    frames = _frames(quse)

    def panel(column: str, ylabel: str, filename: str, *, zero: bool) -> None:
        figure, axis = plt.subplots(figsize=(8, 4.5))
        ymax = 0.0
        picks = _stage_representatives(frames, stage_of, column)
        for stage, number in picks.items():
            frame = frames[number]
            series = frame[column]
            if zero and not series.empty:
                ymax = max(ymax, float(series.max()))
            search = stage == "search"
            axis.plot(
                frame["time-step"],
                series,
                color="#1f4e79" if search else "#9bb7d4",
                linewidth=1.8 if search else 1.0,
                label=f"search q{number}" if search else stage,
            )
        if zero:
            axis.set_ylim(0, 1 if ymax <= 0 else ymax * 1.1)
        axis.set_xlabel("milliseconds from first event")
        axis.set_ylabel(ylabel)
        axis.set_title(f"{title} {ylabel} ({scenario['workers']} search workers)")
        axis.legend(frameon=False)
        figure.tight_layout()
        figure.savefig(out / filename, dpi=120)
        plt.close(figure)

    panel("Utilization(10)", "utilization", "utilization.png", zero=False)
    panel("Saturation(10)", "saturation", "saturation.png", zero=False)
    panel("Errors(10)", "errors", "errors.png", zero=True)
    _plot_latency(sysmetrics, out, title)
    _plot_throughput(sysmetrics, out, title)
    longest = _longest_jobs(analysis / "events.log", set(stage_of), 3)
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
    _fill_metrics(scenario, frames, stage_of, sysmetrics)


def write_summary(rows: list[dict], results: Path, layout: Layout) -> None:
    results.mkdir(parents=True, exist_ok=True)
    first = rows[0]
    visits = expected_search_visits(layout.ready_fail_p, layout.max_ready_visits)
    search_mean = expected_search_mean()
    arrival = first["arrival_mean"]
    ready = ready_rho(arrival, layout.ready_fail_p, layout.max_ready_visits)
    if layout.single_search:
        shape = (
            f"{len(layout.queues)} queues: five shared stages and one search queue. "
            "Skill band still changes the wait."
        )
        shard = "search queue"
    else:
        shape = (
            f"{len(layout.queues)} queues: five shared stages and "
            f"{len(layout.search_queues)} search shards "
            f"({layout.replicas} per bracket)."
        )
        shard = "search shard"
    if "allocate" in layout.swept_stages:
        pool = "Search and allocate are the pools under test. Every other stage has 1 worker."
    else:
        pool = "Search is the pool under test. Every other stage has 1 worker."
    depth = first.get("qsize", layout.qsize)
    lines = [
        f"# {layout.size} matchmaking study",
        "",
        shape,
        f"Each scenario offers {first['count']} tickets.",
        f"Unscaled search mean is ${search_mean:.2f}$ s and a ready retry brings the "
        f"expected search visits to ${visits:.2f}$.",
        f"The unscaled interarrival mean is ${arrival:.3f}$ s so one {shard} "
        f"with {layout.baseline_search_workers} workers sits near "
        f"$\\rho = {layout.target_rho:.1f}$.",
        f"The same arrival puts the single ready worker near $\\rho = {ready:.2f}$. "
        "Staff ready before adding search workers.",
        f"`--time-scale {first['time_scale']}` shortens every sleep, including the interarrival.",
        pool,
        f"Every buffer holds {depth} tickets, so a slower pool queues instead of dropping.",
        "`QUEUE-FULL` is a ticket that never entered a stage. "
        "A ready-check failure is a ticket that reached the failed sink.",
        "Time in the plots is milliseconds from the first event.",
        "The USE panels draw one line per stage. The dark line is the busiest search queue.",
        "",
        "| scenario | search workers | per queue | matched | ready-check failures | QUEUE-FULL | mean latency (ms) | search peak util | search peak sat | ready peak util |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        latency = row["mean_latency"]
        latency_text = f"{latency:.1f}" if latency == latency else ""
        lines.append(
            f"| {row['name']} | {row['workers']} | {row['worker_span']} | {row['matched']} | "
            f"{row['failed']} | {row['queue_full']} | {latency_text} | "
            f"{row['search_util']:.3f} | {row['search_sat']:.1f} | {row['ready_util']:.3f} |"
        )
    lines.extend(
        [
            "",
            "Six plots per scenario, in `results/<scenario>/`:",
            "",
            "- `utilization.png`, `saturation.png`, `errors.png` (`errors.png` counts `QUEUE-FULL` only)",
            "- `latency.png`, `throughput.png`",
            "- `longest-jobs.png` (three longest jobs)",
            "",
            "`sensitivity-servers.png` compares the seven pools: mean latency, "
            "`QUEUE-FULL` and ready-check failures as a percent of tickets, and tickets completed per second. "
            "`sensitivity-latency.png` is that latency series as bars. "
            "The sets are ordered from the smallest search total to the largest.",
            "",
        ]
    )
    (results / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    subject = "search and allocate workers" if "allocate" in layout.swept_stages else "search workers"
    write_sensitivity(rows, results, layout.size, subject)


def write_sensitivity(rows: list[dict], results: Path, size: str, subject: str = "search workers") -> None:
    """Cross-scenario chart. Lab 0 drew latency, drops, and throughput on one figure."""
    ordered = sorted(rows, key=lambda row: (row["workers"], row["name"]))
    labels = [f"{row['name']}\n({row['workers']})" for row in ordered]
    count = float(ordered[0]["count"])
    full = [100.0 * row["queue_full"] / count for row in ordered]
    ready = [100.0 * row["failed"] / count for row in ordered]
    latency = [row["mean_latency"] for row in ordered]
    throughput = [row["throughput"] for row in ordered]

    figure, axes = plt.subplots(1, 3, figsize=(14, 4.2))
    axes[0].plot(labels, latency, color="#2a78d6", marker="o", linewidth=2)
    axes[0].set_ylabel("mean end-to-end latency (ms)")
    axes[0].set_title("Latency")
    axes[1].plot(labels, full, color="#c0392b", marker="o", linewidth=2, label="QUEUE-FULL")
    axes[1].plot(labels, ready, color="#e67e22", marker="o", linewidth=2, label="ready-check failure")
    axes[1].set_ylabel("percent of tickets")
    axes[1].set_title("Errors")
    axes[1].legend(frameon=False, fontsize=8)
    axes[2].plot(labels, throughput, color="#2a9d4a", marker="o", linewidth=2)
    axes[2].set_ylabel("tickets completed per second")
    axes[2].set_title("Throughput")
    for axis in axes:
        axis.set_ylim(bottom=0)
    figure.suptitle(f"Sensitivity: {subject}, {size}", x=0.01, ha="left")
    figure.tight_layout()
    figure.savefig(results / "sensitivity-servers.png", dpi=120)
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(8, 4.5))
    axis.bar(labels, latency, color="#2a78d6")
    axis.set_ylabel("mean end-to-end latency (ms)")
    axis.set_title(f"Server scaling, matchmaking {size} study")
    figure.tight_layout()
    figure.savefig(results / "sensitivity-latency.png", dpi=120)
    plt.close(figure)


def _frames(quse: pd.DataFrame) -> dict[int, pd.DataFrame]:
    frames: dict[int, pd.DataFrame] = {}
    if quse.empty:
        return frames
    for qid, frame in quse.groupby("queue-sys-id"):
        frames[int(qid)] = frame
    return frames


def _stage_representatives(
    frames: dict[int, pd.DataFrame],
    stage_of: dict[int, str],
    column: str,
) -> dict[str, int]:
    """One queue per stage: the shared queue, or the search queue with the highest peak."""
    best: dict[str, tuple[float, int]] = {}
    for number, stage in stage_of.items():
        frame = frames.get(number)
        if frame is None or frame.empty or column not in frame:
            continue
        peak = float(frame[column].max())
        current = best.get(stage)
        if current is None or peak > current[0]:
            best[stage] = (peak, number)
    return {stage: number for stage, (_peak, number) in best.items()}


def _plot_latency(sysmetrics: pd.DataFrame, out: Path, title: str) -> None:
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


def _plot_throughput(sysmetrics: pd.DataFrame, out: Path, title: str) -> None:
    figure, axis = plt.subplots(figsize=(8, 4.5))
    ymax = 0.0
    if not sysmetrics.empty:
        axis.plot(sysmetrics["time-step"], sysmetrics["throughput"], color="#2a9d4a")
        ymax = float(sysmetrics["throughput"].fillna(0).max())
    axis.set_ylim(0, 1 if ymax <= 0 else ymax * 1.1)
    axis.set_xlabel("milliseconds from first event")
    axis.set_ylabel("jobs leaving per millisecond")
    axis.set_title(f"{title} throughput")
    figure.tight_layout()
    figure.savefig(out / "throughput.png", dpi=120)
    plt.close(figure)


def _longest_jobs(events: Path, queue_numbers: set[int], count: int) -> list[str]:
    arrivals: dict[str, int] = {}
    departures: dict[str, int] = {}
    queue_ids = {f"q{number}" for number in queue_numbers}
    sinks = {"matched.csv", "failed.csv", "sink"}
    for line in events.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        kind = parts[4]
        if kind == "ARRIVE-VIA" and parts[1] not in arrivals and parts[5] not in queue_ids:
            arrivals[parts[1]] = int(float(parts[0]))
        elif kind == "DEPART-VIA" and parts[5] in sinks:
            departures[parts[1]] = int(float(parts[0]))
    spans = [
        (departures[job] - arrivals[job], job)
        for job in arrivals
        if job in departures
    ]
    spans.sort(reverse=True)
    return [job for _span, job in spans[:count]]


def _fill_metrics(
    scenario: dict,
    frames: dict[int, pd.DataFrame],
    stage_of: dict[int, str],
    sysmetrics: pd.DataFrame,
) -> None:
    valid = sysmetrics.dropna(subset=["Avg-response-time"]) if not sysmetrics.empty else sysmetrics
    weights = valid["throughput"] if not valid.empty else None
    if weights is not None and float(weights.sum()):
        scenario["mean_latency"] = float((valid["Avg-response-time"] * weights).sum() / weights.sum())
    else:
        scenario["mean_latency"] = float("nan")
    scenario["search_util"] = _stage_peak(frames, stage_of, "search", "Utilization(10)")
    scenario["search_sat"] = _stage_peak(frames, stage_of, "search", "Saturation(10)")
    scenario["ready_util"] = _stage_peak(frames, stage_of, "ready", "Utilization(10)")
    peaks = []
    for number, frame in frames.items():
        if "Utilization(10)" in frame and not frame.empty:
            peaks.append((float(frame["Utilization(10)"].max()), number))
    peaks.sort(reverse=True)
    scenario["busiest"] = f"q{peaks[0][1]}" if peaks else ""
    span_ms = float(sysmetrics["time-step"].max()) if not sysmetrics.empty else float("nan")
    completed = scenario["matched"] + scenario["failed"]
    scenario["span_ms"] = span_ms
    scenario["throughput"] = (
        completed / (span_ms / 1000.0) if span_ms == span_ms and span_ms > 0 else float("nan")
    )


def _stage_peak(
    frames: dict[int, pd.DataFrame],
    stage_of: dict[int, str],
    stage: str,
    column: str,
) -> float:
    peak = 0.0
    seen = False
    for number, name in stage_of.items():
        if name != stage or number not in frames:
            continue
        frame = frames[number]
        if frame.empty or column not in frame:
            continue
        peak = max(peak, float(frame[column].max()))
        seen = True
    if not seen:
        return float("nan")
    return peak
