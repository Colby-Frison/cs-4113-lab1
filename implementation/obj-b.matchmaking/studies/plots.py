"""Six figures and the cross-scenario sensitivity charts for an expanded study.

Utilization, saturation, and errors draw one line per stage. Search is the
busiest search queue, not one line per shard. Ready is drawn in a distinct
warm color so the shared-gate bottleneck is visible next to search.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .arrival import expected_search_mean, expected_search_visits, ready_rho
from .expand import Layout

OBJ_A = Path(__file__).resolve().parents[2] / "obj-a"
TRACE_LIMIT = 40
# Required for the six study figures. Pretty-printed traces are optional: on
# Large/Very Large the events.log is hundreds of MB and pretty-print can OOM WSL.
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
)
OPTIONAL_TOOLS = (
    "pp-events.log",
    "pp-longest.log",
    "longest-job.log",
)

# Stage chrome: ready is the usual end-to-end limiter; search is the swept pool.
STAGE_STYLE = {
    "admit": ("#a0a4ab", 1.0),
    "rating": ("#6e8bb5", 1.1),
    "route": ("#3d6e8a", 1.1),
    "search": ("#1f4e79", 2.3),
    "ready": ("#c0392b", 2.3),
    "allocate": ("#6f8f5a", 1.2),
}
CHROME = {
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
    "ink": "#0b0b0b",
    "muted": "#52514e",
}
# Prefer the 1000-step USE window: stage casts are long; this kills spike hair.
USE_WINDOW = 1000
PLOT_DPI = 140
MAX_POINTS = 2500


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
    for name in OPTIONAL_TOOLS:
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
        figure, axis = plt.subplots(figsize=(8.2, 4.4))
        ymax = 0.0
        picks = _stage_representatives(frames, stage_of, column)
        # Draw quiet stages first so search/ready sit on top.
        order = [stage for stage in ("admit", "rating", "route", "allocate", "search", "ready") if stage in picks]
        for stage in order:
            number = picks[stage]
            frame = frames[number]
            series = frame[column]
            if series.empty:
                continue
            time, values = _downsample(frame["time-step"].to_numpy(), series.to_numpy())
            if zero or len(values):
                ymax = max(ymax, float(np.nanmax(values)) if len(values) else 0.0)
            color, width = STAGE_STYLE.get(stage, ("#9bb7d4", 1.0))
            label = f"search q{number}" if stage == "search" else stage
            axis.plot(time, values, color=color, linewidth=width, label=label, solid_capstyle="round")
        if zero:
            axis.set_ylim(0, 1 if ymax <= 0 else ymax * 1.12)
        elif ymax > 0:
            axis.set_ylim(0, ymax * 1.12)
        _style_axis(axis)
        axis.set_xlabel("milliseconds from first event")
        axis.set_ylabel(ylabel)
        axis.set_title(f"{title} {ylabel} ({scenario['workers']} search workers)")
        axis.legend(frameon=False, fontsize=8, loc="best")
        figure.tight_layout()
        figure.savefig(out / filename, dpi=PLOT_DPI)
        plt.close(figure)

    util_col = f"Utilization({USE_WINDOW})"
    sat_col = f"Saturation({USE_WINDOW})"
    err_col = f"Errors({USE_WINDOW})"
    # Fall back to the 10-step window if an older CSV lacks wider windows.
    if util_col not in quse.columns:
        util_col, sat_col, err_col = "Utilization(10)", "Saturation(10)", "Errors(10)"
    panel(util_col, "utilization", "utilization.png", zero=False)
    panel(sat_col, "saturation", "saturation.png", zero=False)
    panel(err_col, "errors (QUEUE-FULL)", "errors.png", zero=True)
    _plot_stage_use(frames, stage_of, out, title, scenario["workers"], util_col, sat_col, err_col)
    _plot_latency(sysmetrics, out, title)
    _plot_throughput(sysmetrics, out, title)
    _plot_system(sysmetrics, out, title)
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
        "The USE panels draw one line per stage (100-step window). "
        "Search is dark blue; ready is red.",
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
            "- `use.png` (stacked USE with one line per stage; ready red, search dark blue)",
            "- `latency.png`, `throughput.png`, `system.png` (stacked latency + throughput)",
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
    xs = list(range(len(ordered)))
    count = float(ordered[0]["count"])
    full = [100.0 * row["queue_full"] / count for row in ordered]
    ready = [100.0 * row["failed"] / count for row in ordered]
    # Prefer seconds when latencies are large; keeps the panel readable in print.
    latency_ms = [row["mean_latency"] for row in ordered]
    use_seconds = bool(latency_ms) and max(latency_ms) >= 1000.0
    latency = [value / 1000.0 for value in latency_ms] if use_seconds else latency_ms
    latency_label = "mean end-to-end latency (s)" if use_seconds else "mean end-to-end latency (ms)"
    throughput = [row["throughput"] for row in ordered]

    figure, axes = plt.subplots(1, 3, figsize=(13.5, 4.0))
    axes[0].plot(xs, latency, color="#2a78d6", marker="o", linewidth=2.2, markersize=6)
    axes[0].set_ylabel(latency_label)
    axes[0].set_title("Latency")
    _tight_ylim(axes[0], latency, pad=0.08, floor_zero=False)

    axes[1].plot(xs, full, color="#c0392b", marker="o", linewidth=2.0, label="QUEUE-FULL")
    axes[1].plot(xs, ready, color="#e67e22", marker="s", linewidth=2.0, label="ready-check failure")
    axes[1].set_ylabel("percent of tickets")
    axes[1].set_title("Errors")
    axes[1].legend(frameon=False, fontsize=8, loc="best")
    _tight_ylim(axes[1], full + ready, pad=0.15, floor_zero=True)

    axes[2].plot(xs, throughput, color="#2a9d4a", marker="o", linewidth=2.2, markersize=6)
    axes[2].set_ylabel("tickets completed per second")
    axes[2].set_title("Throughput")
    _tight_ylim(axes[2], throughput, pad=0.08, floor_zero=False)

    for axis in axes:
        axis.set_xticks(xs)
        axis.set_xticklabels(labels, fontsize=7.5)
        _style_axis(axis)
    figure.suptitle(f"Sensitivity: {subject}, {size}", x=0.01, ha="left", fontsize=11, color=CHROME["ink"])
    figure.tight_layout(rect=(0, 0, 1, 0.94))
    figure.savefig(results / "sensitivity-servers.png", dpi=PLOT_DPI)
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(8, 4.2))
    axis.bar(xs, latency, color="#2a78d6", width=0.7)
    axis.set_xticks(xs)
    axis.set_xticklabels(labels, fontsize=7.5)
    axis.set_ylabel(latency_label)
    axis.set_title(f"Server scaling, matchmaking {size} study")
    _tight_ylim(axis, latency, pad=0.08, floor_zero=False)
    _style_axis(axis)
    figure.tight_layout()
    figure.savefig(results / "sensitivity-latency.png", dpi=PLOT_DPI)
    plt.close(figure)


def _frames(quse: pd.DataFrame) -> dict[int, pd.DataFrame]:
    frames: dict[int, pd.DataFrame] = {}
    if quse.empty:
        return frames
    for qid, frame in quse.groupby("queue-sys-id"):
        frames[int(qid)] = frame.reset_index(drop=True)
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


def _plot_stage_use(
    frames: dict[int, pd.DataFrame],
    stage_of: dict[int, str],
    out: Path,
    title: str,
    workers: int,
    util_col: str,
    sat_col: str,
    err_col: str,
) -> None:
    """Information-dense USE panel that stays readable under search fan-out.

    One representative queue per stage (busiest search shard). Shared stages stay
    muted; ready and search sit on top in red/blue. A single long USE window
    removes the 10/100/1000 hairball from the generic tool plot.
    """
    picks = _stage_representatives(frames, stage_of, sat_col)
    if not picks:
        picks = _stage_representatives(frames, stage_of, util_col)
    if not picks:
        return
    order = [stage for stage in ("admit", "rating", "route", "allocate", "search", "ready") if stage in picks]
    figure, axes = plt.subplots(3, 1, figsize=(8.4, 7.0), sharex=True, constrained_layout=True)
    panels = (
        (axes[0], util_col, "Utilization", False),
        (axes[1], sat_col, "Saturation", False),
        (axes[2], err_col, "Errors (QUEUE-FULL)", True),
    )
    for axis, column, ylabel, force_zero in panels:
        ymax = 0.0
        for stage in order:
            number = picks[stage]
            frame = frames.get(number)
            if frame is None or frame.empty or column not in frame:
                continue
            time_ms, values = _downsample(
                frame["time-step"].to_numpy(dtype=float),
                frame[column].to_numpy(dtype=float),
            )
            time = time_ms / 1000.0
            if len(values):
                ymax = max(ymax, float(np.nanmax(values)))
            color, width = STAGE_STYLE.get(stage, ("#9bb7d4", 1.0))
            label = f"search q{number}" if stage == "search" else stage
            if stage == "ready" and ylabel.startswith("Saturation"):
                axis.fill_between(time, values, color=color, alpha=0.18, linewidth=0)
            axis.plot(time, values, color=color, linewidth=width, label=label, solid_capstyle="round")
        if force_zero:
            axis.set_ylim(0, 1 if ymax <= 0 else max(1.0, ymax * 1.15))
        elif ymax > 0:
            top = 1.05 if ylabel == "Utilization" and ymax <= 1.05 else ymax * 1.12
            axis.set_ylim(0, top)
        else:
            axis.set_ylim(bottom=0)
        axis.set_ylabel(ylabel)
        _style_axis(axis)
    axes[0].set_title(f"{title} USE ({workers} search workers)", loc="left", fontsize=11)
    axes[0].legend(frameon=False, fontsize=8, ncol=3, loc="upper right")
    axes[2].set_xlabel("seconds from first event")
    # Tiny reading guide so the dense panel still teaches the casting.
    axes[1].text(
        0.01,
        0.92,
        "ready (red) = shared gate · search (blue) = swept pool",
        transform=axes[1].transAxes,
        fontsize=7.5,
        color=CHROME["muted"],
        va="top",
    )
    figure.savefig(out / "use.png", dpi=PLOT_DPI)
    plt.close(figure)


def _plot_latency(sysmetrics: pd.DataFrame, out: Path, title: str) -> None:
    figure, axis = plt.subplots(figsize=(8.2, 4.4))
    if not sysmetrics.empty:
        time = sysmetrics["time-step"].to_numpy(dtype=float)
        median = sysmetrics["median-response-time"].to_numpy(dtype=float) / 1000.0
        average = sysmetrics["Avg-response-time"].to_numpy(dtype=float) / 1000.0
        lo = sysmetrics["10-response-time"].to_numpy(dtype=float) / 1000.0
        hi = sysmetrics["90-response-time"].to_numpy(dtype=float) / 1000.0
        t, median = _downsample(time, median)
        _, average = _downsample(time, average)
        _, lo = _downsample(time, lo)
        _, hi = _downsample(time, hi)
        axis.fill_between(t, lo, hi, color="#2a78d6", alpha=0.18, linewidth=0, label="P10–P90")
        axis.plot(t, median, color="#2a78d6", linewidth=1.8, label="median")
        axis.plot(t, average, color="#eb6834", linewidth=1.8, label="average")
    _style_axis(axis)
    axis.set_xlabel("milliseconds from first event")
    axis.set_ylabel("end-to-end response (s)")
    axis.set_title(f"{title} latency")
    axis.set_ylim(bottom=0)
    axis.legend(frameon=False, fontsize=8)
    figure.tight_layout()
    figure.savefig(out / "latency.png", dpi=PLOT_DPI)
    plt.close(figure)


def _plot_throughput(sysmetrics: pd.DataFrame, out: Path, title: str) -> None:
    figure, axis = plt.subplots(figsize=(8.2, 4.4))
    ymax = 0.0
    if not sysmetrics.empty:
        t_s, rate = _jobs_per_second(sysmetrics)
        axis.plot(t_s, rate, color="#2a9d4a", linewidth=1.8)
        axis.fill_between(t_s, 0, rate, color="#2a9d4a", alpha=0.12, linewidth=0)
        ymax = float(np.nanmax(rate)) if len(rate) else 0.0
    axis.set_ylim(0, 1 if ymax <= 0 else ymax * 1.15)
    _style_axis(axis)
    axis.set_xlabel("milliseconds from first event")
    axis.set_ylabel("tickets completed per second")
    axis.set_title(f"{title} throughput")
    figure.tight_layout()
    figure.savefig(out / "throughput.png", dpi=PLOT_DPI)
    plt.close(figure)


def _plot_system(sysmetrics: pd.DataFrame, out: Path, title: str) -> None:
    """Stacked latency + throughput panel for reports (same data, clearer units)."""
    figure, (ax_lat, ax_tp) = plt.subplots(
        2, 1, figsize=(8.2, 5.6), sharex=True, gridspec_kw={"height_ratios": [3, 2]}
    )
    if not sysmetrics.empty:
        time = sysmetrics["time-step"].to_numpy(dtype=float)
        average = sysmetrics["Avg-response-time"].to_numpy(dtype=float) / 1000.0
        median = sysmetrics["median-response-time"].to_numpy(dtype=float) / 1000.0
        lo = sysmetrics["10-response-time"].to_numpy(dtype=float) / 1000.0
        hi = sysmetrics["90-response-time"].to_numpy(dtype=float) / 1000.0
        t, average = _downsample(time, average)
        _, median = _downsample(time, median)
        _, lo = _downsample(time, lo)
        _, hi = _downsample(time, hi)
        ax_lat.fill_between(t, lo, hi, color="#2a78d6", alpha=0.18, linewidth=0, label="P10–P90")
        ax_lat.plot(t, median, color="#2a78d6", linewidth=1.8, label="median")
        ax_lat.plot(t, average, color="#eb6834", linewidth=1.8, label="average")
        t_s, rate = _jobs_per_second(sysmetrics)
        ax_tp.plot(t_s, rate, color="#2a9d4a", linewidth=1.8)
        ax_tp.fill_between(t_s, 0, rate, color="#2a9d4a", alpha=0.12, linewidth=0)
        ax_tp.set_ylim(0, max(1.0, float(np.nanmax(rate)) * 1.15) if len(rate) else 1.0)
    ax_lat.set_ylabel("response (s)")
    ax_lat.set_title(f"{title} system metrics", loc="left", fontsize=11)
    ax_lat.set_ylim(bottom=0)
    ax_lat.legend(frameon=False, fontsize=8, loc="upper left")
    ax_tp.set_ylabel("tickets / s")
    ax_tp.set_xlabel("milliseconds from first event")
    for axis in (ax_lat, ax_tp):
        _style_axis(axis)
    figure.tight_layout()
    figure.savefig(out / "system.png", dpi=PLOT_DPI)
    plt.close(figure)


def _jobs_per_second(sysmetrics: pd.DataFrame, window_ms: float = 1000.0) -> tuple[np.ndarray, np.ndarray]:
    """Turn per-step departure counts into a readable tickets/s series."""
    time = sysmetrics["time-step"].to_numpy(dtype=float)
    counts = sysmetrics["throughput"].fillna(0).to_numpy(dtype=float)
    if len(time) == 0:
        return np.asarray([]), np.asarray([])
    span = float(time[-1] - time[0]) if len(time) > 1 else 0.0
    if span <= 0:
        return time, counts
    # Cumulative departures, then slope over a fixed window.
    order = np.argsort(time)
    time = time[order]
    cumulative = np.cumsum(counts[order])
    half = window_ms / 2.0
    rate = np.empty_like(time)
    left = 0
    right = 0
    for index, stamp in enumerate(time):
        while left < index and time[left] < stamp - half:
            left += 1
        while right + 1 < len(time) and time[right + 1] <= stamp + half:
            right += 1
        width = max(time[right] - time[left], 1.0)
        departed = cumulative[right] - (cumulative[left - 1] if left > 0 else 0.0)
        rate[index] = 1000.0 * departed / width
    return _downsample(time, rate)


def _downsample(time: np.ndarray, values: np.ndarray, limit: int = MAX_POINTS) -> tuple[np.ndarray, np.ndarray]:
    if len(time) <= limit:
        return time, values
    idx = np.linspace(0, len(time) - 1, limit).astype(int)
    return time[idx], values[idx]


def _tight_ylim(axis, values: list[float], *, pad: float, floor_zero: bool) -> None:
    clean = [float(value) for value in values if value == value]
    if not clean:
        axis.set_ylim(bottom=0)
        return
    low, high = min(clean), max(clean)
    mid = (abs(low) + abs(high)) / 2.0
    relative = 0.0 if mid == 0 else (high - low) / mid
    # Nearly flat sweeps should read as "no change", not a zoomed-in ECG.
    if relative < 0.12:
        axis.set_ylim(0.0, high * 1.18 if high > 0 else 1.0)
        return
    if high == low:
        span = max(abs(high) * 0.1, 1.0)
        low, high = high - span, high + span
    else:
        span = high - low
        low -= span * pad
        high += span * pad
    if floor_zero:
        low = min(0.0, low)
        if high <= 0:
            high = 1.0
    axis.set_ylim(low, high)


def _style_axis(axis) -> None:
    axis.grid(True, color=CHROME["grid"], linewidth=1, linestyle="-")
    axis.set_axisbelow(True)
    for side in ("top", "right"):
        axis.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axis.spines[side].set_color(CHROME["axis"])
    axis.tick_params(colors=CHROME["muted"], labelsize=8)
    axis.yaxis.label.set_color(CHROME["ink"])
    axis.xaxis.label.set_color(CHROME["ink"])
    axis.title.set_color(CHROME["ink"])


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
    util_col = f"Utilization({USE_WINDOW})"
    sat_col = f"Saturation({USE_WINDOW})"
    if frames:
        sample = next(iter(frames.values()))
        if util_col not in sample.columns:
            util_col, sat_col = "Utilization(10)", "Saturation(10)"
    scenario["search_util"] = _stage_peak(frames, stage_of, "search", util_col)
    scenario["search_sat"] = _stage_peak(frames, stage_of, "search", sat_col)
    scenario["ready_util"] = _stage_peak(frames, stage_of, "ready", util_col)
    peaks = []
    for number, frame in frames.items():
        if util_col in frame and not frame.empty:
            peaks.append((float(frame[util_col].max()), number))
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
