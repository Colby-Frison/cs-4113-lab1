#!/usr/bin/env python3
"""Run a matchmaking size study from its pattern file.

Small, Medium, Large, and Very Large share this runner. At most 80 queues
start one queue process and one stage server per queue. Above that, one
queue host and one server host hold every logical queue. The seven scenarios
scale the stages named in the pattern. The others stay at their pattern count.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
MM_ROOT = HERE.parent
REPO = HERE.parents[2]
OBJ_A = REPO / "implementation" / "obj-a"
for _entry in (str(MM_ROOT), str(OBJ_A)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from mm.workload import WorkloadConfig, generate  # noqa: E402

from studies.arrival import (  # noqa: E402
    expected_search_mean,
    expected_search_visits,
    tuned_arrival_mean,
)
from studies.expand import (  # noqa: E402
    Layout,
    QueueSpec,
    backend_for,
    build_layout,
    load_pattern,
    search_worker_total,
    write_bracket_layout,
    write_host_spec,
    write_qconf,
    write_servers_csv,
)
from studies.job_pipe import JobPipe, Tee  # noqa: E402
from studies.plots import TRACE_LIMIT, write_six_plots, write_summary  # noqa: E402
from studies.scale import SCENARIOS, scale_layout, worker_span  # noqa: E402

QUEUE_PY = OBJ_A / "queue" / "queue.py"
STAGE_PY = MM_ROOT / "server-stages" / "server_func.py"
HOST_QUEUE = HERE / "host" / "queue_host.py"
HOST_SERVER = HERE / "host" / "server_host.py"
ANALYZE = OBJ_A / "mockups" / "analyze_run.py"
PORT_BASE = 29100
PORT_STRIDE = 200
LISTEN_TIMEOUT_SEC = 120
PIPE_TIMEOUT_SEC = 60
STALL_SEC = 45


class _Cancelled(Exception):
    """The scenario is stopping before the generator finishes."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one matchmaking size study.")
    parser.add_argument("--size", required=True, choices=("small", "medium", "large", "very-large"))
    parser.add_argument("--only", default="", help="Comma-separated scenario names (default: all seven)")
    parser.add_argument("--count", type=int, default=0, help="Tickets per scenario (default: the pattern)")
    parser.add_argument("--time-scale", type=float, default=-1.0, help="Default: the pattern")
    parser.add_argument("--arrival-mean", type=float, default=-1.0, help="Unscaled seconds. Default: tuned")
    parser.add_argument("--runs", default="", help="Process logs and sinks")
    parser.add_argument("--results", default="", help="Figures and summary.md")
    parser.add_argument("--plots-only", action="store_true")
    args = parser.parse_args(argv)

    pattern = load_pattern(HERE / args.size / "pattern.json")
    base = build_layout(pattern)
    count = args.count if args.count > 0 else base.count
    time_scale = args.time_scale if args.time_scale >= 0 else base.time_scale
    if args.arrival_mean >= 0:
        arrival = args.arrival_mean
    else:
        arrival = tuned_arrival_mean(
            len(base.search_queues),
            base.baseline_search_workers,
            base.target_rho,
            base.ready_fail_p,
            base.max_ready_visits,
        )
    runs = Path(args.runs) if args.runs else HERE / args.size / "runs"
    results = Path(args.results) if args.results else HERE / args.size / "results"
    selected = _select(args.only)
    print(
        f"{base.size}: {len(base.queues)} queues, backend {backend_for(base)}, "
        f"tickets {count}, arrival {arrival:.4f}s",
        flush=True,
    )
    rows = []
    for index, (name, factor) in enumerate(selected):
        layout = scale_layout(base, factor)
        allocate = ""
        if "allocate" in layout.swept_stages:
            workers = next(queue.workers for queue in layout.queues if queue.stage == "allocate")
            allocate = f" allocate workers {workers}"
        print(
            f"scenario {name} backend {backend_for(layout)} "
            f"search workers {search_worker_total(layout)}{allocate}",
            flush=True,
        )
        row = run_scenario(
            name,
            layout,
            index,
            runs,
            results,
            count,
            time_scale,
            arrival,
            plots_only=args.plots_only,
        )
        rows.append(row)
        print(
            f"  matched {row['matched']} failed {row['failed']} queue-full {row['queue_full']} "
            f"latency {row['mean_latency']:.1f} ms",
            flush=True,
        )
    write_summary(rows, results, base)
    print(f"summary: {results / 'summary.md'}", flush=True)
    return 0


def run_scenario(
    name: str,
    layout: Layout,
    scenario_index: int,
    runs: Path,
    results: Path,
    count: int,
    time_scale: float,
    arrival_mean: float,
    *,
    plots_only: bool,
) -> dict:
    run_dir = runs / name
    if count != layout.qsize:
        layout = replace(
            layout,
            qsize=count,
            queues=tuple(replace(queue, qsize=count) for queue in layout.queues),
        )
    if not plots_only:
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True)
        write_servers_csv(layout, run_dir / "servers.csv")
        write_qconf(layout, run_dir / "qconf.csv")
        if backend_for(layout) == "process":
            execute_process(name, layout, scenario_index, run_dir, count, time_scale, arrival_mean)
        else:
            execute_hosted(name, layout, scenario_index, run_dir, count, time_scale, arrival_mean)
        _require_accounted(name, run_dir, count)
    elif not (run_dir / "servers.csv").is_file():
        raise RuntimeError(f"{name}: no run to plot in {run_dir}")

    _run_analysis(name, run_dir, layout)
    row = {
        "name": name,
        "workers": search_worker_total(layout),
        "worker_span": worker_span(layout),
        "count": count,
        "matched": csv_rows(run_dir / "matched.csv"),
        "failed": csv_rows(run_dir / "failed.csv"),
        "queue_full": len(queue_full_jobs(run_dir)),
        "run_dir": run_dir,
        "arrival_mean": arrival_mean,
        "time_scale": time_scale,
        "qsize": layout.qsize,
        "stage_of": {queue.number: queue.stage for queue in layout.queues},
    }
    write_six_plots(row, results)
    return row


def execute_process(
    name: str,
    layout: Layout,
    scenario_index: int,
    run_dir: Path,
    count: int,
    time_scale: float,
    arrival_mean: float,
) -> None:
    ports = _assign_ports(layout, PORT_BASE + scenario_index * PORT_STRIDE)
    destinations = {
        queue.qid: f"127.0.0.1:{ports[queue.qid]},{queue.workers}"
        for queue in layout.search_queues
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    write_bracket_layout(layout, destinations, run_dir / "bracket.json")
    pipe_path = run_dir / "jobs.fifo"
    pipe = JobPipe(pipe_path)
    pipe_name = str(pipe_path)
    children: list[tuple[subprocess.Popen, object]] = []
    cancel = threading.Event()
    box: dict = {}
    generator: threading.Thread | None = None
    try:
        logs = []
        for queue in layout.queues:
            children.append(spawn(_server_args(queue, layout, ports, run_dir), run_dir / f"sf-{queue.qid}.err"))
            logs.append(run_dir / f"sf-{queue.qid}.log")
        for queue in layout.queues:
            if queue.qid == layout.admit_qid:
                continue
            children.append(
                spawn(_queue_args(queue, ports, run_dir, pipe_name), run_dir / f"{queue.qid}.err")
            )
            logs.append(run_dir / f"{queue.qid}.log")
        wait_for_listen(logs, LISTEN_TIMEOUT_SEC)
        admit = next(queue for queue in layout.queues if queue.qid == layout.admit_qid)
        q0, q0_err = spawn(_queue_args(admit, ports, run_dir, pipe_name), run_dir / f"{admit.qid}.err")
        children.append((q0, q0_err))
        pipe.wait_for_reader(PIPE_TIMEOUT_SEC)
        region, mode = _traffic_pins(layout)
        generator = threading.Thread(
            target=_run_generator,
            args=(run_dir, pipe, layout, count, time_scale, arrival_mean, cancel, box, region, mode),
            name=f"gen-{name}",
        )
        generator.start()
        _poll(name, run_dir, count, time_scale, arrival_mean, layout, box, children, q0, generator)
    finally:
        cancel.set()
        pipe.close()
        stop(children)
        if generator is not None:
            generator.join(timeout=10)


def execute_hosted(
    name: str,
    layout: Layout,
    scenario_index: int,
    run_dir: Path,
    count: int,
    time_scale: float,
    arrival_mean: float,
    *,
    region: str | None = None,
    mode: str | None = None,
) -> None:
    spec_path = run_dir / "spec.json"
    run_dir.mkdir(parents=True, exist_ok=True)
    write_host_spec(layout, spec_path)
    base = PORT_BASE + scenario_index * PORT_STRIDE
    queue_port = base
    server_port = base + 1
    pool = max(1, sum(queue.workers for queue in layout.queues))
    pipe_path = run_dir / "jobs.fifo"
    pipe = JobPipe(pipe_path)
    pipe_name = str(pipe_path)
    children: list[tuple[subprocess.Popen, object]] = []
    cancel = threading.Event()
    box: dict = {}
    generator: threading.Thread | None = None
    try:
        children.append(
            spawn(
                [
                    sys.executable,
                    str(HOST_SERVER),
                    "--spec",
                    str(spec_path),
                    "--listen",
                    f"127.0.0.1:{server_port},{pool}",
                    "--queue",
                    f"127.0.0.1:{queue_port}",
                    "--matched",
                    str(run_dir / "matched.csv"),
                    "--failed",
                    str(run_dir / "failed.csv"),
                    "--logfile",
                    str(run_dir / "sf-host.log"),
                    "--state-file",
                    str(run_dir / "ready-state.txt"),
                ],
                run_dir / "sf-host.err",
            )
        )
        wait_for_listen([run_dir / "sf-host.log"], LISTEN_TIMEOUT_SEC)
        listen_workers = min(256, max(32, len(layout.queues)))
        children.append(
            spawn(
                [
                    sys.executable,
                    str(HOST_QUEUE),
                    "--spec",
                    str(spec_path),
                    "--listen",
                    f"127.0.0.1:{queue_port},{listen_workers}",
                    "--server",
                    f"127.0.0.1:{server_port}",
                    "--pipe",
                    pipe_name,
                    "--logfile",
                    str(run_dir / "q-host.log"),
                ],
                run_dir / "q-host.err",
            )
        )
        wait_for_listen([run_dir / "q-host.log"], LISTEN_TIMEOUT_SEC)
        pipe.wait_for_reader(PIPE_TIMEOUT_SEC)
        region, mode = _traffic_pins(layout, region, mode)
        generator = threading.Thread(
            target=_run_generator,
            args=(run_dir, pipe, layout, count, time_scale, arrival_mean, cancel, box, region, mode),
            name=f"gen-{name}",
        )
        generator.start()
        _poll(name, run_dir, count, time_scale, arrival_mean, layout, box, children, None, generator)
    finally:
        cancel.set()
        pipe.close()
        stop(children)
        if generator is not None:
            generator.join(timeout=10)


def _assign_ports(layout: Layout, base: int) -> dict[str, int]:
    port = base
    assigned: dict[str, int] = {}
    for queue in layout.queues:
        if queue.qid != layout.admit_qid:
            assigned[queue.qid] = port
            port += 1
        assigned[f"s:{queue.qid}"] = port
        port += 1
    return assigned


def _queue_args(queue: QueueSpec, ports: dict[str, int], run_dir: Path, pipe_name: str) -> list[str]:
    args = [
        sys.executable,
        str(QUEUE_PY),
        "--qid",
        queue.qid,
        "--qsize",
        str(queue.qsize),
        "--logfile",
        str(run_dir / f"{queue.qid}.log"),
        "--outgrpc",
        f"127.0.0.1:{ports[f's:{queue.qid}']},{queue.workers}",
    ]
    if queue.stage == "admit":
        args.extend(["-i", pipe_name])
    else:
        args.extend(["--ingrpc", f"127.0.0.1:{ports[queue.qid]},{queue.workers}"])
    return args


def _server_args(queue: QueueSpec, layout: Layout, ports: dict[str, int], run_dir: Path) -> list[str]:
    args = [
        sys.executable,
        str(STAGE_PY),
        "--stage",
        queue.server,
        "--qid",
        queue.qid,
        "--server-id",
        "0",
        "--ingrpc",
        f"127.0.0.1:{ports[f's:{queue.qid}']},{queue.workers}",
        "--logfile",
        str(run_dir / f"sf-{queue.qid}.log"),
    ]
    if queue.server == "route":
        args.extend(["--bracket-layout", str(run_dir / "bracket.json")])
    elif queue.server == "ready":
        args.extend(
            [
                "--bracket-layout",
                str(run_dir / "bracket.json"),
                "--pass",
                f"127.0.0.1:{ports[layout.allocate_qid]},1",
                "--fail",
                str(run_dir / "failed.csv"),
                "--ready-fail-p",
                str(layout.ready_fail_p),
                "--max-ready-visits",
                str(layout.max_ready_visits),
                "--state-file",
                str(run_dir / "ready-state.txt"),
                "--seed",
                str(layout.seed),
            ]
        )
    elif queue.server == "allocate":
        args.extend(["--outfile", str(run_dir / "matched.csv")])
    else:
        if queue.next_qid is None:
            raise RuntimeError(f"{queue.qid} has no next queue")
        args.extend(["--outgrpc", f"127.0.0.1:{ports[queue.next_qid]},1"])
    return args


def _traffic_pins(
    layout: Layout,
    region: str | None = None,
    mode: str | None = None,
) -> tuple[str | None, str | None]:
    """Pin the generator when the pattern names one region or one mode.

    A full axis stays unpinned, so tickets spread across that axis.
    """
    if region is None and len(layout.regions) == 1:
        region = layout.regions[0]
    if mode is None and len(layout.modes) == 1:
        mode = layout.modes[0]
    return region, mode


def _run_generator(
    run_dir: Path,
    pipe: JobPipe,
    layout: Layout,
    count: int,
    time_scale: float,
    arrival_mean: float,
    cancel: threading.Event,
    box: dict,
    region: str | None = None,
    mode: str | None = None,
) -> None:
    tee = Tee(run_dir / "jobs.csv", pipe)

    def sleep_fn(delay: float) -> None:
        deadline = time.monotonic() + max(0.0, delay)
        while True:
            if cancel.is_set():
                raise _Cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.2, remaining))

    try:
        box["emitted"] = generate(
            WorkloadConfig(
                time_scale=time_scale,
                arrival_mean=arrival_mean,
                region=region,
                mode=mode,
                count=count,
                seed=layout.seed,
            ),
            tee,
            sleep_fn=sleep_fn,
        )
    except _Cancelled:
        box["cancelled"] = True
    except Exception as exc:  # noqa: BLE001 - the scenario report needs the generator error
        box["error"] = exc
    finally:
        tee.close()


def _poll(
    name: str,
    run_dir: Path,
    count: int,
    time_scale: float,
    arrival_mean: float,
    layout: Layout,
    box: dict,
    children: list[tuple[subprocess.Popen, object]],
    admit_process: subprocess.Popen | None,
    generator: threading.Thread,
) -> None:
    matched = run_dir / "matched.csv"
    failed = run_dir / "failed.csv"
    visits = expected_search_visits(layout.ready_fail_p, layout.max_ready_visits)
    generation = count * arrival_mean * time_scale
    ready = count * visits * 2.0 * time_scale
    search = count * visits * expected_search_mean() * time_scale / max(1, search_worker_total(layout))
    deadline = time.time() + 120 + max(generation, ready, search) * 8
    last_print = 0.0
    last_accounted = -1
    last_change = time.time()
    while time.time() < deadline:
        if box.get("error") is not None:
            raise RuntimeError(f"{name}: generator failed: {box['error']}")
        dead = [
            process
            for process, _handle in children
            if process is not admit_process and process.poll() is not None
        ]
        if dead:
            raise RuntimeError(f"{name}: a listener exited\n{err_tail(run_dir)}")
        ok = csv_rows(matched)
        lost = csv_rows(failed)
        dropped = len(queue_full_jobs(run_dir))
        accounted = ok + lost + dropped
        now = time.time()
        if accounted != last_accounted:
            last_accounted = accounted
            last_change = now
        elif accounted > 0 and now - last_change > STALL_SEC:
            raise RuntimeError(f"{name}: sinks stalled at {accounted}/{count}\n{err_tail(run_dir)}")
        if now - last_print > 5:
            print(f"  sinks matched={ok} failed={lost} queue-full={dropped}", flush=True)
            last_print = now
        if accounted >= count and box.get("emitted") == count:
            time.sleep(0.8)
            return
        time.sleep(0.4)
    ok = csv_rows(matched)
    lost = csv_rows(failed)
    dropped = len(queue_full_jobs(run_dir))
    raise RuntimeError(
        f"{name}: accounted {ok + lost + dropped}/{count} "
        f"(matched={ok} failed={lost} queue-full={dropped})\n{err_tail(run_dir)}"
    )


def _run_analysis(name: str, run_dir: Path, layout: Layout) -> None:
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
    search = layout.search_queues[0].qid
    filtered = subprocess.run(
        [
            sys.executable,
            str(OBJ_A / "filter-by-queue" / "filter_by_queue.py"),
            "--qmet",
            str(run_dir / "analysis" / "qmet.csv"),
            "--qid",
            search,
            "--out",
            str(run_dir / "analysis" / f"{search}-qmet.csv"),
        ],
        cwd=str(REPO),
        check=False,
        capture_output=True,
        text=True,
    )
    if filtered.returncode != 0:
        raise RuntimeError(f"{name}: filter-by-queue {search} failed\n{filtered.stderr[-2000:]}")


def _require_accounted(name: str, run_dir: Path, count: int) -> None:
    matched = csv_rows(run_dir / "matched.csv")
    failed = csv_rows(run_dir / "failed.csv")
    dropped = len(queue_full_jobs(run_dir))
    if matched + failed + dropped != count:
        raise RuntimeError(
            f"{name}: accounted {matched + failed + dropped}/{count} "
            f"(matched={matched} failed={failed} queue-full={dropped})\n{err_tail(run_dir)}"
        )


def _select(only: str) -> list[tuple[str, float]]:
    if not only:
        return list(SCENARIOS)
    wanted = {name.strip() for name in only.split(",") if name.strip()}
    chosen = [item for item in SCENARIOS if item[0] in wanted]
    missing = wanted - {name for name, _factor in chosen}
    if missing:
        raise SystemExit(f"unknown scenario {', '.join(sorted(missing))}")
    return chosen


def spawn(args: list[str], err_path: Path) -> tuple[subprocess.Popen, object]:
    err_path.parent.mkdir(parents=True, exist_ok=True)
    handle = err_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        args,
        cwd=str(REPO),
        stdout=subprocess.DEVNULL,
        stderr=handle,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return process, handle


def stop(processes: list[tuple[subprocess.Popen, object]]) -> None:
    for process, _handle in processes:
        if process.poll() is None:
            process.kill()
    for process, handle in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        handle.close()


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


def csv_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    rows = 0
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.strip() and not line.startswith("Job Id"):
            rows += 1
    return rows


def queue_full_jobs(run_dir: Path) -> set[str]:
    found: set[str] = set()
    for path in sorted(run_dir.glob("*.log")):
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "QUEUE-FULL" not in line:
                continue
            parts = line.split(" - ")[-1].split()
            if len(parts) >= 2:
                found.add(parts[1])
    return found


def err_tail(run_dir: Path) -> str:
    chunks: list[str] = []
    for path in sorted(run_dir.glob("*.err")):
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        if text:
            chunks.append(f"--- {path.name}\n{text[-500:]}")
        if len(chunks) == 6:
            break
    return "\n".join(chunks)


if __name__ == "__main__":
    raise SystemExit(main())
