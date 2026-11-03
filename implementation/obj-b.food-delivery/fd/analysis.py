"""Explicit topology conversion and order accounting, including downstream drops."""
import csv
import importlib.util
import json
import statistics
import sys

from fd import OBJ_A
from lib.job import iter_jobs


def adapter():
    name = "food_delivery_obj_a_analysis"
    spec = importlib.util.spec_from_file_location(name, OBJ_A / "mockups/analyze_run.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def analyze(root, config, mode, *, plots=False):
    bridge = adapter()
    records = bridge.load_records(root)
    # Both queues have a server. A Q departure is only a transport handoff.
    records = [r for r in records if not (r.component == "Q" and r.kind == "DEPART-VIA")]
    for record in records:
        if record.kind == "ARRIVE-VIA":
            record.rest = "source" if record.qsid == "q0" else "q0"
        elif record.kind == "DEPART-VIA":
            record.rest = "q1" if mode == "stages" and record.qsid == "q0" else "sink"
    # Shared events round timestamps to milliseconds. Different process log
    # filenames must not make an arrival sort after its server entry on a tie.
    def phase(record):
        ranks = {"ARRIVE-VIA": 0, "ENTERS-SERVER": 1, "EXITS-SERVER": 100, "DEPART-VIA": 101}
        if record.kind in ("TASK-START", "TASK-END"):
            return 2 + int(record.rest.split()[0]) * 2 + (record.kind == "TASK-END")
        return ranks.get(record.kind, 102)
    records.sort(key=lambda r: (r.time, r.job, r.qsid, phase(r), r.order))
    output = root / "analysis"
    output.mkdir()
    bridge.write_events(records, output / "events.log")
    bridge.write_qmet(records, output / "qmet.csv")
    specs = {"q0": (config["kitchen_capacity"], config["kitchen_workers"])}
    if mode == "stages":
        specs["q1"] = (config["pickup_capacity"], config["couriers"])
    bridge.write_qconf(records, output / "qconf.csv", specs)

    with (root / "jobs.csv").open() as handle:
        jobs = list(iter_jobs(handle))
    generated = {job.job_id: job for job in jobs}
    delivered = []
    for path in sorted(root.glob("sink-*.csv")):
        with path.open() as handle:
            delivered.extend(iter_jobs(handle))
    delivered_ids = {job.job_id for job in delivered}
    if len(delivered_ids) != len(delivered) or not delivered_ids <= generated.keys():
        raise ValueError("duplicate or unknown delivered order")
    if any(job.raw_line != generated[job.job_id].raw_line for job in delivered):
        raise ValueError("order changed in transit")
    dropped = {r.job for r in records if r.kind == "ERROR" and r.rest == "QUEUE-FULL"}
    if dropped & delivered_ids or not dropped <= generated.keys():
        raise ValueError("invalid drop accounting")
    incomplete = generated.keys() - delivered_ids - dropped
    completed = {r.job: r.time for r in records if r.kind == "DEPART-VIA" and r.rest == "sink"}
    prepared = {r.job: r.time for r in records if r.kind == "EXITS-SERVER" and r.qsid == "q0"}
    picked_up = {r.job: r.time for r in records if r.kind == "ENTERS-SERVER" and r.qsid == "q1"}
    if set(completed) != delivered_ids:
        raise ValueError("sink rows and completion events disagree")
    latencies, pickup_waits = [], []
    with (output / "orders.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["order_id", "status", "end_to_end_seconds", "pickup_wait_seconds"])
        for job in jobs:
            latency = max(0, completed[job.job_id] - job.arrival_time) if job.job_id in completed else None
            pickup = max(0, picked_up[job.job_id] - prepared[job.job_id]) if job.job_id in picked_up else None
            if latency is not None:
                latencies.append(latency)
            if pickup is not None:
                pickup_waits.append(pickup)
            status = "delivered" if job.job_id in delivered_ids else "dropped" if job.job_id in dropped else "incomplete"
            writer.writerow([job.job_id, status, latency, pickup])
    elapsed = max((r.time for r in records), default=0) - min((j.arrival_time for j in jobs), default=0)
    summary = {
        "mode": mode, "generated": len(jobs), "delivered": len(delivered),
        "dropped": len(dropped), "incomplete": len(incomplete),
        "mean_latency_seconds": statistics.mean(latencies) if latencies else None,
        "mean_pickup_wait_seconds": statistics.mean(pickup_waits) if pickup_waits else None,
        "throughput_orders_per_second": len(delivered) / elapsed if elapsed > 0 else None,
        "observation_seconds": max(0, elapsed),
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    if incomplete:
        raise RuntimeError(f"{len(incomplete)} orders unaccounted for; see {output}")
    if plots and delivered:
        bridge.run_tools(output, "Food delivery", trace_limit=40)
    return summary
