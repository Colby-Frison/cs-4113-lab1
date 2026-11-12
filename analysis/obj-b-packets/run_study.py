#!/usr/bin/env python3
"""Packet routing study of one model: a baseline and six server budgets.

A model is a folder next to this script with a ``study.json``, for example
``simple`` or ``large``. Every set offers the same packets at the same rate and
keeps the same buffers, so servers are the only thing that changes. Each set's
budget is split in proportion to each router's predicted load. Each set runs once
per traffic seed. The first seed runs all the Objective A tools and gives the
set's six plots, and the other seeds add to the metrics table.

The main plots integrate queue state over time. Averaging rows would be wrong here,
because the rows are written when a queue changes rather than at a fixed interval.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
APP = REPO / "implementation" / "obj-b.packets"
OBJ_A = REPO / "implementation" / "obj-a"
FIREWALL = REPO / "implementation" / "obj-b.firewall"
sys.path[:0] = [str(APP), str(OBJ_A), str(OBJ_A / "mockups")]

import analyze_run
import build_network
import run_local

BIN_SECONDS = 1.0
TRACE_LIMIT = 40
LONGEST_JOBS = 3
TOOL_OUTPUTS = (
    "events.log", "qmet.csv", "qconf.csv", "q0-qmet.csv", "quse.csv", "use.png",
    "emetrics.csv", "sysmetrics.csv", "resplot.png", "response-hist.png",
    "response-scatter.png", "etrace.png", "pp-events.log", "pp-longest.log", "longest-job.log",
)
# Bigger tool outputs stay in the run archive, so results fit a home directory quota.
LARGEST_COPIED_BYTES = 50_000_000
# Above this many queues, the USE plot and the summary table show only the busiest.
LISTED_QUEUES = 10
FIREWALL_KEYS = ("firewall_rules", "rule_wait", "blocked_share")
# Slots 1 to 3 of the validated categorical palette, then neutral inks.
SERIES = ("#2a78d6", "#eb6834", "#1baf7a")
OTHER = "#b9b8b0"
INK = "#3b3b37"
GRID = "#e6e5df"


def plan_study(study: dict) -> dict:
    """The rate, and each set's servers, predicted from the routing tables."""
    spec = base_spec(study)
    graph = build_network.load_graph(APP / spec["topology"])
    nodes = build_network.random_walk_sample(graph, spec["routers"], spec["seed"])
    links = build_network.induced_links(graph, nodes)
    tables = build_network.build_tables(links)
    sources = build_network.choose_sources(links, spec["sources"], spec["seed"])
    visits = build_network.visit_rates(tables, sources)
    mode = study.get("mode", build_network.MONOLITH)
    # A firewall adds N * r to the visit that checks it. Denied packets, a share p,
    # make only that visit, at their source router.
    check = study.get("firewall_rules", 0) * study.get("rule_wait", 0.0)
    denied = study.get("blocked_share", 0.0) if study.get("firewall_rules") else 0.0
    source_share = [1 / len(sources) if router in sources else 0.0 for router in range(len(visits))]

    def checked(router: int) -> float:
        return (1 - denied) * visits[router] + denied * source_share[router]

    def allowed(router: int) -> float:
        return (1 - denied) * visits[router]

    # A decomposed router has one queue per stage, and each stage has its own service time.
    stages = (
        [(spec["validate_wait"] + check, checked), (spec["lookup_wait"], allowed),
         (spec["forward_wait_mean"], allowed)]
        if mode == build_network.STAGES
        else [(spec["validate_wait"] + spec["lookup_wait"] + spec["forward_wait_mean"] + check, checked)]
    )
    # (queue id, router, service time, packets per offered packet)
    queues = [
        (build_network.queue_id(router, mode, stage), router, wait, arrivals(router))
        for router in range(len(visits))
        for stage, (wait, arrivals) in enumerate(stages)
    ]
    baseline = build_network.allocate_servers(visits, study["baseline_servers"])
    rate = study["target_busiest_utilization"] / max(
        load * wait / baseline[router] for _, router, wait, load in queues
    )
    sets = {}
    for name, factor in study["sets"].items():
        total = round(study["baseline_servers"] * factor)
        servers = build_network.allocate_servers(visits, total)
        sets[name] = {
            "factor": factor,
            "total_servers": total,
            "servers": servers,
            "predicted_utilization": [
                rate * load * wait / servers[router] for _, router, wait, load in queues
            ],
        }
    # The baseline's likeliest bottlenecks keep their colors in every set.
    baseline_load = sets["base"]["predicted_utilization"]
    order = sorted(range(len(queues)), key=lambda q: (-baseline_load[q], q))
    busiest = [queues[q][0] for q in order[:LISTED_QUEUES]]
    return {
        "mode": mode,
        "rate": rate,
        "visits": visits,
        "queues": [queue[0] for queue in queues],
        "queue_router": [queue[1] for queue in queues],
        "blocked_share": denied,
        "busiest": busiest,
        "highlight": busiest[: len(SERIES)],
        "sets": sets,
    }


def queue_servers(plan: dict, name: str) -> dict[str, int]:
    """Each queue's servers in one set. A router's stage queues share its count."""
    servers = plan["sets"][name]["servers"]
    return {qid: servers[router] for qid, router in zip(plan["queues"], plan["queue_router"])}


def base_spec(study: dict) -> dict:
    keys = (
        "routers", "seed", "topology", "sources", "degree_per_server", "qsize_per_server",
        "min_qsize", "validate_wait", "lookup_wait", "forward_wait_mean", "ttl",
    )
    spec = {key: study[key] for key in keys}
    # Firewall settings pass through only when a study has them.
    spec.update({key: study[key] for key in FIREWALL_KEYS if key in study})
    return spec


def read_events(path: Path):
    """Yield ``(milliseconds, job, queue, kind, detail)`` from a converted event log."""
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            parts = line.split(maxsplit=5)
            if len(parts) >= 5 and parts[2] == "IN":
                yield int(parts[0]), parts[1], parts[3], parts[4], parts[5].strip() if len(parts) > 5 else ""


def delivered_latencies(path: Path) -> dict[str, tuple[int, int]]:
    """Each delivered job's ``(arrival, delivery)`` in milliseconds."""
    arrivals: dict[str, int] = {}
    delivered: dict[str, int] = {}
    for when, job, _, kind, detail in read_events(path):
        if kind == "ARRIVE-VIA" and job not in arrivals:
            arrivals[job] = when
        elif kind == "DEPART-VIA" and detail.startswith("sink-"):
            delivered[job] = when
    return {job: (arrivals[job], when) for job, when in delivered.items() if job in arrivals}


def time_weighted(rows: list[tuple[int, int, int]], end: int, bin_ms: int) -> dict[str, list[float]]:
    """Bin one queue's state. ``rows`` are ``(time, waiting, serving)`` in time order.

    Each value holds until the next row, so a bin's value is the mean over its time.
    """
    bins = max(1, math.ceil(end / bin_ms))
    waiting = [0.0] * bins
    serving = [0.0] * bins
    for index, (start, wait_count, serve_count) in enumerate(rows):
        stop = rows[index + 1][0] if index + 1 < len(rows) else end
        position = start
        while position < stop:
            current = min(position // bin_ms, bins - 1)
            edge = min(stop, (current + 1) * bin_ms)
            if edge <= position:
                break
            waiting[current] += wait_count * (edge - position)
            serving[current] += serve_count * (edge - position)
            position = edge
    return {
        "waiting": [value / bin_ms for value in waiting],
        "serving": [value / bin_ms for value in serving],
    }


def read_queue_state(path: Path) -> tuple[dict[str, list], dict[str, list[int]]]:
    """Per-queue state rows and error times from ``qmet.csv``."""
    state: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    errors: dict[str, list[int]] = defaultdict(list)
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            queue = f"q{row['queue-sys-id']}"
            when = int(row["time-step"])
            state[queue].append((when, int(row["num-jobs-in-queue"]), int(row["num-jobs-in-service"])))
            if int(row["num-of-errors"]):
                errors[queue].append(when)
    return state, errors


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


def run_metrics(
    run_dir: Path, events: Path, qmet: Path, servers: dict[str, int], summary: dict
) -> dict:
    latencies = delivered_latencies(events)
    durations = [done - start for start, done in latencies.values()]
    state, _ = read_queue_state(qmet)
    end = max(rows[-1][0] for rows in state.values())
    utilization = {}
    for queue, count in servers.items():
        rows = state.get(queue, [(0, 0, 0)])
        area = sum(
            serve * ((rows[i + 1][0] if i + 1 < len(rows) else end) - when)
            for i, (when, _, serve) in enumerate(rows)
        )
        utilization[queue] = area / (count * end) if end else 0.0
    sent = summary["sent"]
    arrival_times = []
    for path in run_dir.glob("jobs-*.csv"):
        with path.open(encoding="utf-8", newline="") as handle:
            arrival_times += [float(row[1]) for row in list(csv.reader(handle))[1:]]
    span = max(arrival_times) - min(arrival_times)
    # Firewall denials are intended, so loss counts only the packets the firewall allowed.
    denied = summary.get("FIREWALL-DENY", 0)
    allowed = sent - denied
    return {
        "sent": sent,
        "delivered": summary["delivered"],
        "denied": denied,
        "dropped": summary["QUEUE-FULL"],
        "routing_faults": summary["TTL-EXPIRED"] + summary["NO-OUT-EDGE"],
        "unaccounted": summary["unaccounted"],
        "loss_percent": 100 * (allowed - summary["delivered"]) / allowed if allowed else 0.0,
        "offered_per_second": (sent - 1) / span if span else 0.0,
        "delivered_per_second": 1000 * summary["delivered"] / end if end else 0.0,
        "mean_latency_ms": statistics.mean(durations) if durations else float("nan"),
        "median_latency_ms": statistics.median(durations) if durations else float("nan"),
        "p90_latency_ms": percentile(durations, 0.9) if durations else float("nan"),
        "utilization": utilization,
    }


def longest_jobs(events: Path, count: int) -> list[str]:
    latencies = delivered_latencies(events)
    ranked = sorted(latencies, key=lambda job: latencies[job][1] - latencies[job][0], reverse=True)
    return ranked[:count]


def style(axis, title: str, ylabel: str) -> None:
    axis.set_title(title, loc="left", fontsize=12, color=INK)
    axis.set_xlabel("seconds from first event", color=INK)
    axis.set_ylabel(ylabel, color=INK)
    axis.grid(axis="y", color=GRID, linewidth=0.8)
    axis.set_axisbelow(True)
    axis.tick_params(colors=INK, length=0)
    for side in ("top", "right", "left"):
        axis.spines[side].set_visible(False)
    axis.spines["bottom"].set_color(GRID)


def write_six_plots(
    name: str, analysis: Path, out: Path, plan: dict, servers: dict[str, int]
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    state, errors = read_queue_state(analysis / "qmet.csv")
    end = max(rows[-1][0] for rows in state.values())
    bin_ms = int(BIN_SECONDS * 1000)
    bins = max(1, math.ceil(end / bin_ms))
    centers = [(index + 0.5) * BIN_SECONDS for index in range(bins)]
    queues = {queue: time_weighted(rows, end, bin_ms) for queue, rows in state.items()}
    error_bins: dict[str, list[int]] = {}
    for queue in state:
        counts = [0] * bins
        for when in errors.get(queue, []):
            counts[min(when // bin_ms, bins - 1)] += 1
        error_bins[queue] = counts

    def queue_plot(values: dict[str, list[float]], title: str, ylabel: str, filename: str) -> None:
        figure, axis = plt.subplots(figsize=(8, 4.5))
        others = [queue for queue in values if queue not in plan["highlight"]]
        for position, queue in enumerate(others):
            axis.plot(centers, values[queue], color=OTHER, linewidth=1,
                      label="other queues" if position == 0 else None)
        for queue, color in zip(plan["highlight"], SERIES):
            if queue in values:
                axis.plot(centers, values[queue], color=color, linewidth=2, label=queue)
        style(axis, f"{name}: {title} per queue", ylabel)
        axis.legend(frameon=False, ncols=4, loc="upper center", bbox_to_anchor=(0.5, -0.14))
        figure.tight_layout()
        figure.savefig(out / filename, dpi=130)
        plt.close(figure)

    queue_plot(
        {
            queue: [value / servers[queue] for value in series["serving"]]
            for queue, series in queues.items()
        },
        "utilization", "share of servers busy", "utilization.png",
    )
    queue_plot(
        {queue: series["waiting"] for queue, series in queues.items()},
        "saturation", "packets waiting", "saturation.png",
    )
    queue_plot(error_bins, "errors", "packets dropped per second", "errors.png")

    latencies = delivered_latencies(analysis / "events.log")
    by_bin: dict[int, list[int]] = defaultdict(list)
    for start, done in latencies.values():
        by_bin[min(done // bin_ms, bins - 1)].append(done - start)

    figure, axis = plt.subplots(figsize=(8, 4.5))
    filled = sorted(by_bin)
    x_values = [centers[index] for index in filled]
    axis.fill_between(
        x_values,
        [percentile(by_bin[i], 0.1) for i in filled],
        [percentile(by_bin[i], 0.9) for i in filled],
        color=SERIES[0], alpha=0.18, linewidth=0, label="P10 to P90",
    )
    axis.plot(x_values, [statistics.median(by_bin[i]) for i in filled], color=SERIES[0], linewidth=2, label="median")
    axis.plot(x_values, [statistics.mean(by_bin[i]) for i in filled], color=SERIES[1], linewidth=2, label="mean")
    style(axis, f"{name}: end-to-end latency of delivered packets", "latency (ms)")
    axis.set_ylim(bottom=0)
    axis.legend(frameon=False, ncols=3, loc="upper center", bbox_to_anchor=(0.5, -0.14))
    figure.tight_layout()
    figure.savefig(out / "latency.png", dpi=130)
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(8, 4.5))
    axis.plot(centers, [len(by_bin.get(i, [])) / BIN_SECONDS for i in range(bins)], color=SERIES[0], linewidth=2)
    style(axis, f"{name}: throughput", "packets delivered per second")
    axis.set_ylim(bottom=0)
    figure.tight_layout()
    figure.savefig(out / "throughput.png", dpi=130)
    plt.close(figure)

    longest = longest_jobs(analysis / "events.log", LONGEST_JOBS)
    filtered = out / "longest-jobs.log"
    subprocess.run(
        [sys.executable, str(OBJ_A / "filter-by-job" / "filter_by_job.py"), "--events",
         str(analysis / "events.log"), "--jobs", ",".join(longest), "--out", str(filtered)],
        check=True,
    )
    subprocess.run(
        [sys.executable, str(OBJ_A / "create-event-trace" / "create_event_trace.py"), "--events",
         str(filtered), "--out", str(out / "longest-jobs.png")],
        check=True,
    )


def write_comparison(rows: list[dict], plan: dict, results: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    order = sorted({row["set"] for row in rows}, key=lambda s: plan["sets"][s]["total_servers"])
    panels = (
        ("mean_latency_ms", "Mean latency (ms)"),
        ("p90_latency_ms", "P90 latency (ms)"),
        ("delivered_per_second", "Delivered packets per second"),
        ("loss_percent", "Loss (%)"),
    )
    figure, axes = plt.subplots(2, 2, figsize=(10, 6.5), sharex=True)
    for axis, (key, title) in zip(axes.flat, panels):
        for index, name in enumerate(order):
            values = [row[key] for row in rows if row["set"] == name and not math.isnan(row[key])]
            if not values:
                continue
            mean = statistics.mean(values)
            axis.plot([index, index], [min(values), max(values)], color=SERIES[0], linewidth=2, alpha=0.4)
            axis.plot(index, mean, "o", color=SERIES[0], markersize=8)
            axis.annotate(f"{mean:.0f}" if mean >= 10 else f"{mean:.1f}", (index, mean),
                          textcoords="offset points", xytext=(0, 9), ha="center", color=INK, fontsize=9)
        axis.set_title(title, loc="left", fontsize=11, color=INK)
        axis.grid(axis="y", color=GRID, linewidth=0.8)
        axis.set_axisbelow(True)
        axis.tick_params(colors=INK, length=0)
        for side in ("top", "right", "left"):
            axis.spines[side].set_visible(False)
        axis.spines["bottom"].set_color(GRID)
        axis.margins(y=0.18)
        axis.set_xticks(range(len(order)))
        axis.set_xticklabels(
            [f"{plan['sets'][n]['total_servers']}\n{n}" for n in order], color=INK, fontsize=9
        )
    figure.supxlabel("total servers", color=INK, fontsize=10)
    figure.tight_layout()
    figure.savefig(results / "sensitivity.png", dpi=130)
    plt.close(figure)


def write_results(rows: list[dict], plan: dict, study: dict, results: Path, host: str) -> None:
    fields = [
        "set", "traffic_seed", "server", "total_servers", "sent", "delivered", "denied", "dropped",
        "routing_faults", "unaccounted", "loss_percent", "offered_per_second",
        "delivered_per_second", "mean_latency_ms", "median_latency_ms", "p90_latency_ms",
    ]
    with (results / "metrics-by-run.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: round(value, 3) if isinstance(value, float) else value
                for key, value in row.items()
            })

    order = [name for name in study["sets"] if any(row["set"] == name for row in rows)]
    mean = lambda name, key: statistics.mean(r[key] for r in rows if r["set"] == name)
    lines = [
        f"# {study['name']} study results",
        "",
        f"Generated by `run_study.py` on `{host}`. {study['routers']} routers and "
        f"{len(plan['queues'])} queues in {plan['mode']} mode. "
        f"Requested rate {plan['rate']:.1f} packets per second, "
        f"{study['packets']} packets per run, server functions in {rows[0]['server']}.",
        "Each value is the mean over the runs of that set.",
    ]
    if study.get("firewall_rules"):
        lines.append(
            f"Firewall: {study['firewall_rules']} rules at {1000 * study.get('rule_wait', 0):g} ms each, "
            f"{100 * plan['blocked_share']:g}% of packets sent to blocked hosts. "
            "Loss counts only packets the firewall allowed. Denied packets are in their own column."
        )
    lines += [
        "",
        "| Set | Servers | Runs | Offered /s | Delivered /s | Mean latency (ms) | Median (ms) | P90 (ms) | Loss (%) | Denied | Dropped | Faults | Unaccounted |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name in order:
        count = sum(1 for r in rows if r["set"] == name)
        lines.append(
            f"| {name} | {plan['sets'][name]['total_servers']} | {count} "
            f"| {mean(name, 'offered_per_second'):.1f} | {mean(name, 'delivered_per_second'):.1f} "
            f"| {mean(name, 'mean_latency_ms'):.0f} | {mean(name, 'median_latency_ms'):.0f} "
            f"| {mean(name, 'p90_latency_ms'):.0f} | {mean(name, 'loss_percent'):.2f} "
            f"| {mean(name, 'denied'):.0f} | {mean(name, 'dropped'):.0f} "
            f"| {mean(name, 'routing_faults'):.0f} | {mean(name, 'unaccounted'):.0f} |"
        )
    every = plan["queues"]
    queues = every if len(every) <= LISTED_QUEUES else plan["busiest"]
    position = {queue: index for index, queue in enumerate(every)}
    lines += [
        "",
        "## Predicted and measured utilization",
        "",
        "Predicted from the routing tables at the requested rate. Measured over each run, including start-up and drain, so it should sit somewhat lower.",
    ]
    if queues is not every:
        lines.append(
            f"The table lists the {len(queues)} busiest of {len(every)} queues at baseline. "
            "`plan.json` has every queue's servers and prediction."
        )
    lines += [
        "",
        "| Set | " + " | ".join(queues) + " |",
        "| --- |" + " ---: |" * len(queues),
    ]
    for name in order:
        servers = queue_servers(plan, name)
        predicted = plan["sets"][name]["predicted_utilization"]
        measured = [
            statistics.mean(r["utilization"][queue] for r in rows if r["set"] == name) for queue in queues
        ]
        lines.append(f"| {name} servers | " + " | ".join(str(servers[q]) for q in queues) + " |")
        lines.append(
            f"| {name} predicted | " + " | ".join(f"{predicted[position[q]]:.2f}" for q in queues) + " |"
        )
        lines.append(f"| {name} measured | " + " | ".join(f"{m:.2f}" for m in measured) + " |")
    (results / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (results / "plan.json").write_text(json.dumps(plan, indent=1) + "\n", encoding="utf-8")
    write_comparison(rows, plan, results)


def extension_for(study: dict):
    """The firewall application's additions for a study with a firewall, else none."""
    if not study.get("firewall_rules"):
        return None
    if str(FIREWALL) not in sys.path:
        sys.path.insert(0, str(FIREWALL))
    from fw.extension import FirewallExtension

    return FirewallExtension()


def run_one(name: str, seed: int, first: bool, study: dict, plan: dict, args) -> dict:
    entry = plan["sets"][name]
    spec = base_spec(study)
    spec.update(
        total_servers=entry["total_servers"],
        buffer_servers=study["baseline_servers"],
        traffic_seed=seed,
    )
    run_dir = args.work_dir / name / f"seed-{seed}"
    summary = run_local.run_network(
        spec, plan["mode"], run_dir, plan["rate"], packets=study["packets"],
        server_bin=args.server_bin, extension=extension_for(study),
    )
    servers = queue_servers(plan, name)
    if first:
        # One line per queue is unreadable past a few queues, so plot-use gets the busiest.
        use_queues = (
            None if len(plan["queues"]) <= LISTED_QUEUES else [q[1:] for q in plan["busiest"]]
        )
        analysis = analyze_run.analyze(
            run_dir, analyze_run.load_specs(run_dir / "queues.csv"), TRACE_LIMIT, use_queues
        )
    else:
        analysis = run_dir / "analysis"
        analysis.mkdir()
        records = analyze_run.prepare(analyze_run.load_records(run_dir))
        analyze_run.write_events(records, analysis / "events.log")
        analyze_run.write_qmet(records, analysis / "qmet.csv")
    metrics = run_metrics(run_dir, analysis / "events.log", analysis / "qmet.csv", servers, summary)
    if first:
        out = args.results / name
        if out.exists():
            shutil.rmtree(out)
        (out / "tools").mkdir(parents=True)
        kept_back = []
        for filename in TOOL_OUTPUTS:
            source = analysis / filename
            if source.stat().st_size > LARGEST_COPIED_BYTES:
                kept_back.append(f"{filename} ({source.stat().st_size / 1e6:.0f} MB)")
            else:
                shutil.copy2(source, out / "tools" / filename)
        if kept_back:
            (out / "tools" / "not-copied.txt").write_text(
                "These outputs were too big for the results folder. They are in the run's "
                f"archive under {name}/seed-{seed}/analysis/:\n" + "\n".join(kept_back) + "\n",
                encoding="utf-8",
            )
        write_six_plots(name, analysis, out, plan, servers)
    if args.archive is not None:
        # Packing each run as soon as it is analyzed keeps the node's disk to one run.
        args.archive.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["tar", "czf", str(args.archive / f"{name}-seed-{seed}.tgz"),
             "-C", str(args.work_dir), f"{name}/seed-{seed}"],
            check=True,
        )
        shutil.rmtree(run_dir)
        if not any(run_dir.parent.iterdir()):
            run_dir.parent.rmdir()
    return {
        "set": name, "traffic_seed": seed, "server": summary["server"],
        "total_servers": entry["total_servers"], **metrics,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "model",
        help="Folder next to this script with a study.json, such as simple, "
        "or one under analysis/, such as obj-b-firewall/simple",
    )
    parser.add_argument("--study", type=Path, help="Default: MODEL/study.json")
    parser.add_argument("--only", default="", help="Comma-separated sets (default: all)")
    parser.add_argument("--seeds", default="", help="Comma-separated traffic seeds (default: the study's)")
    parser.add_argument("--packets", type=int, help="Override the study's packets per run")
    parser.add_argument("--server-bin", help="C++ packet_server (default: Python server functions)")
    parser.add_argument("--work-dir", type=Path, help="Where process logs and FIFOs go. Default: MODEL/runs")
    parser.add_argument("--results", type=Path, help="Default: MODEL/results")
    parser.add_argument(
        "--archive", type=Path,
        help="Pack each run's raw logs into this folder, then delete them (default: keep them)",
    )
    args = parser.parse_args(argv)
    # A bare name is a packet routing model. A path is a model of another application.
    model = HERE.parent / args.model if "/" in args.model else HERE / args.model
    args.study = args.study or model / "study.json"
    args.work_dir = args.work_dir or model / "runs"
    args.results = args.results or model / "results"
    args.archive = args.archive.resolve() if args.archive else None
    if not args.study.is_file():
        parser.error(f"no study at {args.study}")

    study = json.loads(args.study.read_text(encoding="utf-8"))
    study["name"] = args.model
    if args.packets is not None:
        study["packets"] = args.packets
    wanted = [name.strip() for name in args.only.split(",") if name.strip()]
    unknown = [name for name in wanted if name not in study["sets"]]
    if unknown:
        parser.error(f"unknown sets {unknown}. Known: {list(study['sets'])}")
    names = wanted or list(study["sets"])
    seeds = [int(seed) for seed in args.seeds.split(",") if seed.strip()] or study["traffic_seeds"]

    plan = plan_study(study)
    print(f"rate {plan['rate']:.1f} packets/s, highlight {plan['highlight']}", flush=True)
    args.results.mkdir(parents=True, exist_ok=True)
    args.work_dir = args.work_dir.resolve()
    host = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()

    rows: list[dict] = []
    # Every set's first seed comes first, so a job that runs out of time still has all sets.
    for index, seed in enumerate(seeds):
        for name in names:
            started = time.time()
            row = run_one(name, seed, index == 0, study, plan, args)
            rows.append(row)
            print(
                f"{name} seed {seed}: sent {row['sent']} delivered {row['delivered']} "
                f"denied {row['denied']} dropped {row['dropped']} unaccounted {row['unaccounted']} "
                f"mean {row['mean_latency_ms']:.0f} ms ({time.time() - started:.0f} s)",
                flush=True,
            )
            write_results(rows, plan, study, args.results, host)
    print(f"summary: {args.results / 'summary.md'}")
    return 1 if any(row["unaccounted"] or row["routing_faults"] for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
