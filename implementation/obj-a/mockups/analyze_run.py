#!/usr/bin/env python3
"""Turn one mock-up run directory into the Objective A analysis inputs, then run the tools.

The queue and server-function logs are process logs. The analysis tools want one
simulator event log (integer time, ``time job IN qid KIND detail``) plus
``qmet.csv`` and ``qconf.csv``. This script builds those files, then calls
filter, derive, plot, trace, and pretty-print on them.

Time in the generated log is milliseconds since the first kept event. A queue
that has a server function logs ``DEPART-VIA`` when it hands the job to that
function, before ``ENTERS-SERVER`` on a FIFO. Those handoffs are left out so a
visit stays open until ``EXITS-SERVER``. A ``DEPART-VIA`` that is followed by
another arrival of the same job is rewritten to that next queue id, so
end-to-end metrics treat only the last departure (the sink file) as leaving
the system.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

OBJ_A = Path(__file__).resolve().parents[1]
EVENT_RE_PREFIX = " - "
QCONF_HEADER = (
    "Sys ID,Queue Max Size,Num. Servers,Server Function,"
    "Server Function Rate,Source,Sink,Edges in,Edges out"
)


@dataclass
class Record:
    time: float
    job: str
    qsid: str
    kind: str
    rest: str
    component: str
    order: int
    step: int = 0


def _message(line: str) -> tuple[str, str] | None:
    """Return ``(component, message)`` from one Python log line."""
    parts = line.rstrip("\n").split(" - ", 3)
    if len(parts) != 4:
        return None
    component = parts[1]
    if component == "Q" or component.startswith("Q_"):
        who = "Q"
    elif component == "sv" or component.startswith("sv_"):
        who = "sv"
    else:
        return None
    return who, parts[3].strip()


def _parse_event(message: str, component: str, order: int) -> Record | None:
    pieces = message.split(maxsplit=5)
    if len(pieces) < 5 or pieces[2] != "IN":
        return None
    try:
        when = float(pieces[0])
    except ValueError:
        return None
    rest = pieces[5].strip() if len(pieces) > 5 else ""
    return Record(when, pieces[1], pieces[3], pieces[4], rest, component, order)


def load_records(run_dir: Path) -> list[Record]:
    records: list[Record] = []
    order = 0
    for path in sorted(run_dir.glob("*.log")):
        for line in path.read_text(encoding="utf-8").splitlines():
            parsed = _message(line)
            if parsed is None:
                continue
            component, message = parsed
            record = _parse_event(message, component, order)
            order += 1
            if record is not None:
                records.append(record)
    if not records:
        raise SystemExit(f"no Lab 0 events under {run_dir}")
    return records


def prepare(records: list[Record]) -> list[Record]:
    """Drop local server handoffs and name internal hops by queue id.

    Pairing is by hop order, not by timestamp. A gRPC receiver logs ARRIVE-VIA
    before the sender logs DEPART-VIA, so "the next arrival after this
    departure" walks past the hop that already happened.
    """
    server_qs = {record.qsid for record in records if record.kind == "ENTERS-SERVER"}
    kept = [
        record
        for record in records
        if not (
            record.component == "Q"
            and record.kind == "DEPART-VIA"
            and record.qsid in server_qs
        )
    ]
    kept.sort(key=lambda record: (record.time, record.order))
    queue_ids = {record.qsid for record in kept}
    by_job: dict[str, list[Record]] = defaultdict(list)
    for record in kept:
        by_job[record.job].append(record)

    for events in by_job.values():
        arrives = [event for event in events if event.kind == "ARRIVE-VIA"]
        departs = [event for event in events if event.kind == "DEPART-VIA"]
        internal_arrives = arrives[1:]
        internal_departs = departs[:-1]
        for depart, arrive in zip(internal_departs, internal_arrives):
            depart.rest = arrive.qsid
            if arrive.rest not in queue_ids:
                arrive.rest = depart.qsid
        for depart in internal_departs[len(internal_arrives) :]:
            depart.rest = depart.qsid
        for arrive in internal_arrives[len(internal_departs) :]:
            if arrive.rest not in queue_ids:
                arrive.rest = arrive.qsid
        if departs and departs[-1].rest in queue_ids:
            departs[-1].rest = "sink"
    return kept


def write_events(records: list[Record], path: Path) -> None:
    origin = min(record.time for record in records)
    lines: list[str] = []
    for record in records:
        record.step = int(round((record.time - origin) * 1000))
        detail = f" {record.rest}" if record.rest else ""
        lines.append(
            f"{record.step} {record.job} IN {record.qsid} {record.kind}{detail}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_qmet(records: list[Record], path: Path) -> None:
    """One sample for the queue whose count just changed.

    Waiting ends at ENTERS-SERVER, or at DEPART when there is no server.
    A row is written for that queue only. Repeating every other queue at the
    same timestamp copies an unchanged count and, at medium scale, multiplies
    the file by the queue count. Each queue still has a zero row at step 0.
    """
    server_qs = {record.qsid for record in records if record.kind == "ENTERS-SERVER"}
    queues = sorted({record.qsid for record in records})
    waiting: dict[str, int] = defaultdict(int)
    serving: dict[str, int] = defaultdict(int)
    rows: list[tuple[int, str, int, int, int]] = [(0, qsid, 0, 0, 0) for qsid in queues]

    for record in records:
        errors = 0
        qsid = record.qsid
        if record.kind == "ARRIVE-VIA":
            waiting[qsid] += 1
        elif record.kind == "ENTERS-SERVER":
            if waiting[qsid] > 0:
                waiting[qsid] -= 1
            serving[qsid] += 1
        elif record.kind == "EXITS-SERVER":
            if serving[qsid] > 0:
                serving[qsid] -= 1
        elif record.kind == "DEPART-VIA" and qsid not in server_qs:
            if waiting[qsid] > 0:
                waiting[qsid] -= 1
        elif record.kind == "ERROR" and record.rest.split()[:1] == ["QUEUE-FULL"]:
            errors = 1
        else:
            continue
        rows.append((record.step, qsid, waiting[qsid], serving[qsid], errors))

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            [
                "time-step",
                "queue-sys-id",
                "num-jobs-in-queue",
                "num-jobs-in-service",
                "num-of-errors",
            ]
        )
        for row in rows:
            qid = row[1][1:] if row[1].startswith("q") and row[1][1:].isdigit() else row[1]
            writer.writerow((row[0], qid, row[2], row[3], row[4]))


def write_qconf(
    records: list[Record],
    path: Path,
    specs: dict[str, tuple[int, int]] | None = None,
) -> None:
    """Write queue config. ``specs`` maps a qid to ``(qsize, servers)``."""
    queues = sorted({record.qsid for record in records})
    lines = [QCONF_HEADER]
    for qsid in queues:
        qsize, servers = (8, 1) if not specs or qsid not in specs else specs[qsid]
        lines.append(f"{qsid},{qsize},{servers},wait,1,,,")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_specs(path: Path) -> dict[str, tuple[int, int]]:
    """Read ``qid,qsize,servers`` rows written by the medium runner."""
    specs: dict[str, tuple[int, int]] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            specs[row["qid"]] = (int(row["qsize"]), int(row["servers"]))
    if not specs:
        raise SystemExit(f"no queue rows in {path}")
    return specs


def _tool(script: str, args: list[str]) -> None:
    subprocess.run([sys.executable, str(OBJ_A / script), *args], check=True)


def _longest_job(emetrics: Path) -> str | None:
    with emetrics.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return None
    return max(rows, key=lambda row: int(row["turnaround-time"]))["Job-id"]


def run_tools(out: Path, title: str, trace_limit: int | None = None) -> None:
    events = out / "events.log"
    qmet = out / "qmet.csv"
    qconf = out / "qconf.csv"
    quse = out / "quse.csv"
    emetrics = out / "emetrics.csv"
    sysmetrics = out / "sysmetrics.csv"

    _tool(
        "filter-by-queue/filter_by_queue.py",
        ["--qmet", str(qmet), "--qid", "0", "--out", str(out / "q0-qmet.csv")],
    )
    _tool(
        "derive-event-metrics/derive_event_metrics.py",
        ["--events", str(events), "--out", str(emetrics), "-v"],
    )
    _tool(
        "derive-sys-metrics/derive_sys_metrics.py",
        ["--events", str(events), "--out", str(sysmetrics), "-v"],
    )
    _tool(
        "compute-use/compute_use.py",
        ["--qmet", str(qmet), "--config", str(qconf), "--out", str(quse), "-v"],
    )
    _tool(
        "plot-use/plot_use.py",
        ["--quse", str(quse), "--out", str(out / "use.png"), "--title", f"{title} USE"],
    )
    _tool(
        "plot-sys-metrics/plot_sys_metrics.py",
        [
            "--sysmetrics",
            str(sysmetrics),
            "--out",
            str(out / "resplot.png"),
            "--title",
            f"{title} response and throughput",
        ],
    )
    _tool(
        "plot-derive-event-metrics/plot_derive_event_metrics.py",
        [
            str(emetrics),
            str(out / "response-hist.png"),
            "--hist",
            "turnaround-time",
            "--bins",
            "6",
            "--title",
            f"{title} visit turnaround",
        ],
    )
    _tool(
        "plot-derive-event-metrics/plot_derive_event_metrics.py",
        [
            str(emetrics),
            str(out / "response-scatter.png"),
            "--scat",
            "arrival-time,turnaround-time",
            "--title",
            f"{title} turnaround by arrival",
        ],
    )
    trace_args = ["--events", str(events), "--out", str(out / "etrace.png"), "-v"]
    if trace_limit is not None:
        trace_args.extend(["--limit", str(trace_limit)])
    _tool("create-event-trace/create_event_trace.py", trace_args)
    # Pretty-printing a full Large/Very Large events.log (100k+ tickets) can
    # OOM a WSL guest and take the VM down. Skip the full dump past 32 MiB;
    # still pretty-print the single longest job below.
    events_bytes = events.stat().st_size if events.is_file() else 0
    if events_bytes <= 32 * 1024 * 1024:
        try:
            _tool(
                "pretty-print-job-trace/pretty_print_events.py",
                [str(events), "--out", str(out / "pp-events.log"), "--no-color"],
            )
        except subprocess.CalledProcessError as exc:
            print(f"warning: pretty-print events skipped ({exc})", flush=True)
    else:
        print(
            f"warning: pretty-print events skipped ({events_bytes} byte events.log)",
            flush=True,
        )
    longest = _longest_job(emetrics)
    if longest is not None:
        try:
            _tool(
                "filter-by-job/filter_by_job.py",
                ["--events", str(events), "--jobs", longest, "--out", str(out / "longest-job.log")],
            )
            _tool(
                "pretty-print-job-trace/pretty_print_events.py",
                [str(out / "longest-job.log"), "--out", str(out / "pp-longest.log"), "--no-color"],
            )
        except subprocess.CalledProcessError as exc:
            print(f"warning: longest-job pretty-print skipped ({exc})", flush=True)


def analyze(
    run_dir: Path,
    specs: dict[str, tuple[int, int]] | None = None,
    trace_limit: int | None = None,
) -> Path:
    records = prepare(load_records(run_dir))
    out = run_dir / "analysis"
    out.mkdir(parents=True, exist_ok=True)
    write_events(records, out / "events.log")
    write_qmet(records, out / "qmet.csv")
    write_qconf(records, out / "qconf.csv", specs)
    run_tools(out, run_dir.parent.name, trace_limit)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze one mock-up run directory.")
    parser.add_argument("run_dir", type=Path, help="Directory that holds the process logs")
    parser.add_argument(
        "--qconf-file",
        type=Path,
        default=None,
        help="CSV of qid,qsize,servers. Missing queues stay at qsize 8 and 1 server.",
    )
    parser.add_argument(
        "--trace-limit",
        type=int,
        default=None,
        help="Plot only the first N jobs in etrace.png. The longest-job trace is separate.",
    )
    args = parser.parse_args(argv)
    specs = load_specs(args.qconf_file) if args.qconf_file is not None else None
    out = analyze(args.run_dir.resolve(), specs, args.trace_limit)
    print(f"analysis: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
