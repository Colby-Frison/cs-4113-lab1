#!/usr/bin/env python3
"""Small matchmaking study: six stage queues and seven server pools.

One scenario starts the Objective A queue and the Python stage server, twelve
processes. gRPC links every hop after admit. The generator writes ``jobs.csv``
and the same bytes to a named pipe that ``q0`` reads. A regular file returns
EOF as soon as the reader catches the writer, which would turn the arrival
process into a burst. The pipe is the Windows stand-in for a FIFO on that one
edge. Stage edges stay on gRPC so a ready-check retry can return to search.

Listeners bind before the generator starts. Search and allocate use the pool
under test. The other stages stay at one worker. After ``matched.csv`` and
``failed.csv`` account for every ticket (``QUEUE-FULL`` counts as well), the
listeners stop and the Objective A tools draw the six figures.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import math
import os
import shutil
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

REPO = Path(__file__).resolve().parents[4]
MM_ROOT = REPO / "implementation" / "obj-b.matchmaking"
OBJ_A = REPO / "implementation" / "obj-a"
HERE = Path(__file__).resolve().parent

sys.path.insert(0, str(MM_ROOT))
sys.path.insert(0, str(OBJ_A))

from mm.model import (  # noqa: E402
    PARTY_SEARCH_FACTOR,
    PARTY_WEIGHTS,
    READY_WAIT,
    SEARCH_MEAN,
    _BAND_CUTS,
)
from mm.workload import WorkloadConfig, generate  # noqa: E402

QUEUE_PY = OBJ_A / "queue" / "queue.py"
STAGE_PY = MM_ROOT / "server-stages" / "server_func.py"
ANALYZE = OBJ_A / "mockups" / "analyze_run.py"

# Logged ids are numeric because compute-use casts the queue id to int.
# The README maps them back onto the stage names.
STAGES = (
    ("admit", "validate"),
    ("rating", "rating"),
    ("route", "route"),
    ("search", "search"),
    ("ready", "ready"),
    ("allocate", "allocate"),
)
SEARCH_INDEX = 3
ALLOCATE_INDEX = 5
READY_INDEX = 4
SWEPT = {SEARCH_INDEX, ALLOCATE_INDEX}

TICKETS = 1000
TIME_SCALE = 0.02
TARGET_RHO = 0.8
BASE_POOL = 10
READY_FAIL_P = 0.2
MAX_READY_VISITS = 2
SEED = 7
REGION = "na"
MODE = "ranked"
TRACE_LIMIT = 40
LISTEN_TIMEOUT_SEC = 120
PIPE_TIMEOUT_SEC = 60

# Distinct integers: floor is what makes 10*0.9, 10*0.8, and 10*0.7 differ,
# and floor(10*1.25) is 12.
SCENARIOS: list[tuple[str, int]] = [
    ("base", 10),
    ("srv-10", 9),
    ("srv-20", 8),
    ("srv-30", 7),
    ("srv+25", 12),
    ("srv+50", 15),
    ("srv+100", 20),
]

PORT_BASE = 28100
PORT_STRIDE = 20

TOOL_FILES = (
    "events.log",
    "qmet.csv",
    "qconf.csv",
    "q0-qmet.csv",
    "q3-qmet.csv",
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

_KERNEL = ctypes.WinDLL("kernel32", use_last_error=True)
_INVALID_HANDLE = wintypes.HANDLE(-1).value
_ERROR_PIPE_CONNECTED = 535
_PIPE_ACCESS_OUTBOUND = 0x00000002

_CreateNamedPipeW = _KERNEL.CreateNamedPipeW
_CreateNamedPipeW.restype = wintypes.HANDLE
_CreateNamedPipeW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
]
_ConnectNamedPipe = _KERNEL.ConnectNamedPipe
_ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_ConnectNamedPipe.restype = wintypes.BOOL
_WriteFile = _KERNEL.WriteFile
_WriteFile.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
]
_WriteFile.restype = wintypes.BOOL
_CloseHandle = _KERNEL.CloseHandle
_CloseHandle.argtypes = [wintypes.HANDLE]
_CloseHandle.restype = wintypes.BOOL


class _Cancelled(Exception):
    """The scenario is stopping before the generator finishes."""


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def expected_search_mean() -> float:
    """Unscaled mean search wait, including the band cuts and the party factor."""
    names = [name for _cut, name in _BAND_CUTS] + ["apex"]
    edges = [-math.inf, *[cut for cut, _name in _BAND_CUTS], math.inf]
    band_mean = 0.0
    for index, name in enumerate(names):
        left = 0.0 if math.isinf(edges[index]) else _phi(edges[index])
        right = 1.0 if math.isinf(edges[index + 1]) else _phi(edges[index + 1])
        band_mean += (right - left) * SEARCH_MEAN[name]
    large = sum(weight for size, weight in PARTY_WEIGHTS if int(size) > 2)
    small = sum(weight for size, weight in PARTY_WEIGHTS if int(size) <= 2)
    return band_mean * ((small + large * PARTY_SEARCH_FACTOR) / (small + large))


def expected_search_visits(fail_p: float, max_visits: int) -> float:
    """Search visits per ticket. A failure below the cap comes back once more."""
    total = 0.0
    chance = 1.0
    for _ in range(max_visits):
        total += chance
        chance *= fail_p
    return total


def tuned_arrival_mean() -> float:
    """Unscaled interarrival that puts the baseline pool, including retries, near rho 0.8.

    $\\rho = \\lambda E[S] E[V] / c$, so the mean interarrival is $E[S] E[V] / (\\rho c)$.
    """
    visits = expected_search_visits(READY_FAIL_P, MAX_READY_VISITS)
    offered = expected_search_mean() * visits
    return offered / (TARGET_RHO * BASE_POOL)


class JobPipe:
    """Byte pipe. ``q0`` opens ``name``; this object writes the generator stream."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._lock = threading.Lock()
        handle = _CreateNamedPipeW(name, _PIPE_ACCESS_OUTBOUND, 0, 1, 65536, 65536, 0, None)
        if int(handle) == int(_INVALID_HANDLE):
            raise OSError(f"CreateNamedPipe {name} failed ({ctypes.get_last_error()})")
        self._handle: int | None = handle

    def wait_for_reader(self, timeout: float) -> None:
        """Block until ``q0`` opens the pipe, or raise when ``timeout`` passes."""
        if self._handle is None:
            raise OSError("job pipe is already closed")
        box: dict[str, int | bool] = {}

        def connect() -> None:
            ok = _ConnectNamedPipe(self._handle, None)
            box["ok"] = bool(ok)
            box["code"] = int(ctypes.get_last_error())

        thread = threading.Thread(target=connect, name="pipe-connect")
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            raise TimeoutError(f"{self.name} had no reader after {timeout:.0f}s")
        if box.get("ok") or box.get("code") == _ERROR_PIPE_CONNECTED:
            return
        raise OSError(f"ConnectNamedPipe {self.name} failed ({box.get('code')})")

    def write(self, text: str) -> int:
        with self._lock:
            handle = self._handle
        if handle is None:
            raise OSError("job pipe is already closed")
        data = text.encode("utf-8")
        written = wintypes.DWORD()
        ok = _WriteFile(handle, data, len(data), ctypes.byref(written), None)
        if not ok:
            raise OSError(f"pipe write failed ({ctypes.get_last_error()})")
        return len(text)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        with self._lock:
            handle = self._handle
            self._handle = None
        if handle is not None:
            _CloseHandle(handle)


class _Tee:
    """Write the generator stream to ``jobs.csv`` and to the pipe ``q0`` reads."""

    def __init__(self, saved: Path, pipe: JobPipe) -> None:
        self._file = saved.open("w", encoding="utf-8", newline="")
        self._pipe = pipe

    def write(self, text: str) -> int:
        self._file.write(text)
        self._file.flush()
        self._pipe.write(text)
        return len(text)

    def flush(self) -> None:
        self._file.flush()

    def close(self) -> None:
        self._file.close()
        self._pipe.close()


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
    """Job ids dropped with ``ERROR QUEUE-FULL`` in the process logs."""
    found: set[str] = set()
    for path in sorted(run_dir.glob("*.log")):
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "QUEUE-FULL" not in line:
                continue
            message = line.split(" - ")[-1]
            parts = message.split()
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


def _transport_tail(run_dir: Path) -> str:
    """First gRPC submit failures. Those lines live in the process logs, not stderr."""
    chunks: list[str] = []
    for path in sorted(run_dir.glob("*.log")):
        hits = [
            line
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if "gRPC submit" in line
        ]
        if hits:
            chunks.append(f"--- {path.name}\n" + "\n".join(hits[:2]))
        if len(chunks) == 3:
            break
    return "\n".join(chunks)


def _pool_for(index: int, pool: int) -> int:
    return pool if index in SWEPT else 1


def _assign_ports(scenario_index: int) -> dict[str, int]:
    """Listen ports. ``q0`` has none; it reads the job pipe."""
    port = PORT_BASE + scenario_index * PORT_STRIDE
    assigned: dict[str, int] = {}
    for index in range(len(STAGES)):
        if index > 0:
            assigned[f"q{index}"] = port
            port += 1
        assigned[f"s{index}"] = port
        port += 1
    return assigned


def _dial(port: int, workers: int) -> str:
    return f"127.0.0.1:{port},{workers}"


def _listen(port: int, workers: int) -> str:
    return f"127.0.0.1:{port},{workers}"


def _queue_args(
    index: int,
    pool: int,
    qsize: int,
    ports: dict[str, int],
    run_dir: Path,
    pipe_name: str,
) -> list[str]:
    qid = f"q{index}"
    workers = _pool_for(index, pool)
    args = [
        sys.executable,
        str(QUEUE_PY),
        "--qid",
        qid,
        "--qsize",
        str(qsize),
        "--logfile",
        str(run_dir / f"{qid}.log"),
        "--outgrpc",
        _dial(ports[f"s{index}"], workers),
    ]
    if index == 0:
        args.extend(["-i", pipe_name])
    else:
        args.extend(["--ingrpc", _listen(ports[qid], workers)])
    return args


def _server_args(
    index: int,
    pool: int,
    ports: dict[str, int],
    run_dir: Path,
) -> list[str]:
    _stage_name, stage = STAGES[index]
    qid = f"q{index}"
    workers = _pool_for(index, pool)
    args = [
        sys.executable,
        str(STAGE_PY),
        "--stage",
        stage,
        "--qid",
        qid,
        "--server-id",
        "0",
        "--ingrpc",
        _listen(ports[f"s{index}"], workers),
        "--logfile",
        str(run_dir / f"sf-{qid}.log"),
    ]
    if stage == "route":
        args.extend(["--route-edge", f"{REGION}={_dial(ports['q3'], 1)}"])
    elif stage == "ready":
        args.extend(
            [
                "--pass",
                _dial(ports["q5"], 1),
                "--retry",
                _dial(ports["q3"], 1),
                "--fail",
                str(run_dir / "failed.csv"),
                "--ready-fail-p",
                str(READY_FAIL_P),
                "--max-ready-visits",
                str(MAX_READY_VISITS),
                "--state-file",
                str(run_dir / "ready-state.txt"),
                "--seed",
                str(SEED),
            ]
        )
    elif stage == "allocate":
        args.extend(["--outfile", str(run_dir / "matched.csv")])
    else:
        args.extend(["--outgrpc", _dial(ports[f"q{index + 1}"], 1)])
    return args


def _start_listeners(
    pool: int,
    qsize: int,
    ports: dict[str, int],
    run_dir: Path,
    pipe_name: str,
) -> tuple[list[tuple[subprocess.Popen, object]], list[Path]]:
    """Bind every gRPC port, then start ``q0`` on the job pipe."""
    children: list[tuple[subprocess.Popen, object]] = []
    listen_logs: list[Path] = []
    for index in range(len(STAGES)):
        log_path = run_dir / f"sf-q{index}.log"
        children.append(spawn(_server_args(index, pool, ports, run_dir), run_dir / f"sf-q{index}.err"))
        listen_logs.append(log_path)
    for index in range(1, len(STAGES)):
        children.append(
            spawn(_queue_args(index, pool, qsize, ports, run_dir, pipe_name), run_dir / f"q{index}.err")
        )
        listen_logs.append(run_dir / f"q{index}.log")
    wait_for_listen(listen_logs, LISTEN_TIMEOUT_SEC)
    return children, listen_logs


def _run_generator(
    run_dir: Path,
    pipe: JobPipe,
    count: int,
    time_scale: float,
    arrival_mean: float,
    cancel: threading.Event,
    box: dict,
) -> None:
    tee = _Tee(run_dir / "jobs.csv", pipe)

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
                region=REGION,
                mode=MODE,
                count=count,
                seed=SEED,
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


def _deadline(count: int, pool: int, time_scale: float, arrival_mean: float) -> float:
    visits = expected_search_visits(READY_FAIL_P, MAX_READY_VISITS)
    generation = count * arrival_mean * time_scale
    ready = count * visits * READY_WAIT * time_scale
    search = count * visits * expected_search_mean() * time_scale / pool
    return time.time() + 120 + max(generation, ready, search) * 8


def execute_pipeline(
    name: str,
    pool: int,
    scenario_index: int,
    run_dir: Path,
    count: int,
    time_scale: float,
    arrival_mean: float,
) -> None:
    ports = _assign_ports(scenario_index)
    pipe_name = rf"\\.\pipe\mm-small-{name}-{os_pid()}"
    pipe = JobPipe(pipe_name)
    children: list[tuple[subprocess.Popen, object]] = []
    cancel = threading.Event()
    box: dict = {}
    generator: threading.Thread | None = None
    try:
        children, _logs = _start_listeners(pool, count, ports, run_dir, pipe_name)
        q0, q0_err = spawn(_queue_args(0, pool, count, ports, run_dir, pipe_name), run_dir / "q0.err")
        children.append((q0, q0_err))
        pipe.wait_for_reader(PIPE_TIMEOUT_SEC)
        generator = threading.Thread(
            target=_run_generator,
            args=(run_dir, pipe, count, time_scale, arrival_mean, cancel, box),
            name=f"gen-{name}",
        )
        generator.start()
        matched = run_dir / "matched.csv"
        failed = run_dir / "failed.csv"
        deadline = _deadline(count, pool, time_scale, arrival_mean)
        last_print = 0.0
        last_accounted = -1
        last_change = time.time()
        while time.time() < deadline:
            if box.get("error") is not None:
                raise RuntimeError(f"{name}: generator failed: {box['error']}")
            dead = [process for process, _handle in children if process is not q0 and process.poll() is not None]
            if dead:
                raise RuntimeError(f"{name}: a listener exited\n{err_tail(run_dir)}")
            if generator is not None and not generator.is_alive() and q0.poll() is not None and box.get("emitted") is None:
                raise RuntimeError(f"{name}: q0 exited before the generator finished\n{err_tail(run_dir)}")
            ok = csv_rows(matched)
            lost = csv_rows(failed)
            dropped = len(queue_full_jobs(run_dir))
            accounted = ok + lost + dropped
            now = time.time()
            if accounted != last_accounted:
                last_accounted = accounted
                last_change = now
            elif accounted > 0 and now - last_change > 45:
                raise RuntimeError(
                    f"{name}: sinks stalled at {accounted}/{count}\n{err_tail(run_dir)}\n{_transport_tail(run_dir)}"
                )
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
    finally:
        cancel.set()
        pipe.close()
        stop(children)
        if generator is not None:
            generator.join(timeout=10)


def os_pid() -> int:
    return os.getpid()


def run_scenario(
    name: str,
    pool: int,
    scenario_index: int,
    runs: Path,
    count: int,
    time_scale: float,
    arrival_mean: float,
    *,
    plots_only: bool,
) -> dict:
    run_dir = runs / name
    if not plots_only:
        if run_dir.exists():
            shutil.rmtree(run_dir)
        run_dir.mkdir(parents=True)
        with (run_dir / "servers.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(["qid", "qsize", "servers"])
            for index in range(len(STAGES)):
                writer.writerow([f"q{index}", count, _pool_for(index, pool)])
        execute_pipeline(name, pool, scenario_index, run_dir, count, time_scale, arrival_mean)
        matched = csv_rows(run_dir / "matched.csv")
        failed = csv_rows(run_dir / "failed.csv")
        dropped = len(queue_full_jobs(run_dir))
        if matched + failed + dropped != count:
            raise RuntimeError(
                f"{name}: accounted {matched + failed + dropped}/{count} "
                f"(matched={matched} failed={failed} queue-full={dropped})\n{err_tail(run_dir)}"
            )
    elif not (run_dir / "servers.csv").is_file():
        raise RuntimeError(f"{name}: no run to plot in {run_dir}")

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
    filtered = subprocess.run(
        [
            sys.executable,
            str(OBJ_A / "filter-by-queue" / "filter_by_queue.py"),
            "--qmet",
            str(run_dir / "analysis" / "qmet.csv"),
            "--qid",
            "q3",
            "--out",
            str(run_dir / "analysis" / "q3-qmet.csv"),
        ],
        cwd=str(REPO),
        check=False,
        capture_output=True,
        text=True,
    )
    if filtered.returncode != 0:
        raise RuntimeError(f"{name}: filter-by-queue q3 failed\n{filtered.stderr[-2000:]}")
    return {
        "name": name,
        "pool": pool,
        "count": count,
        "qsize": count,
        "matched": csv_rows(run_dir / "matched.csv"),
        "failed": csv_rows(run_dir / "failed.csv"),
        "queue_full": len(queue_full_jobs(run_dir)),
        "run_dir": run_dir,
        "arrival_mean": arrival_mean,
        "time_scale": time_scale,
    }


def _longest_jobs(events: Path, count: int) -> list[str]:
    arrivals: dict[str, int] = {}
    departures: dict[str, int] = {}
    queue_ids = {f"q{index}" for index in range(len(STAGES))}
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


def _peak(frame: pd.DataFrame, column: str) -> float:
    if frame.empty:
        return float("nan")
    return float(frame[column].max())


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

    def panel(column: str, ylabel: str, filename: str, *, zero: bool) -> None:
        figure, axis = plt.subplots(figsize=(8, 4.5))
        ymax = 0.0
        for qid, frame in quse.groupby("queue-sys-id"):
            highlight = int(qid) == SEARCH_INDEX
            series = frame[column]
            if zero and not series.empty:
                ymax = max(ymax, float(series.max()))
            axis.plot(
                frame["time-step"],
                series,
                color="#1f4e79" if highlight else "#9bb7d4",
                linewidth=1.6 if highlight else 0.8,
                alpha=1.0 if highlight else 0.45,
                label="q3 search" if highlight else None,
                zorder=2 if highlight else 1,
            )
        if zero:
            axis.set_ylim(0, 1 if ymax <= 0 else ymax * 1.1)
        axis.set_xlabel("milliseconds from first event")
        axis.set_ylabel(ylabel)
        axis.set_title(f"{title} {ylabel} (pool {scenario['pool']})")
        axis.legend(frameon=False)
        figure.tight_layout()
        figure.savefig(out / filename, dpi=120)
        plt.close(figure)

    panel("Utilization(10)", "utilization", "utilization.png", zero=False)
    panel("Saturation(10)", "saturation", "saturation.png", zero=False)
    panel("Errors(10)", "errors", "errors.png", zero=True)

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
    valid = sysmetrics.dropna(subset=["Avg-response-time"]) if not sysmetrics.empty else sysmetrics
    weights = valid["throughput"] if not valid.empty else None
    if weights is not None and float(weights.sum()):
        scenario["mean_latency"] = float((valid["Avg-response-time"] * weights).sum() / weights.sum())
    else:
        scenario["mean_latency"] = float("nan")
    search = quse[quse["queue-sys-id"].astype(int) == SEARCH_INDEX] if not quse.empty else quse
    ready = quse[quse["queue-sys-id"].astype(int) == READY_INDEX] if not quse.empty else quse
    scenario["search_util"] = _peak(search, "Utilization(10)")
    scenario["search_sat"] = _peak(search, "Saturation(10)")
    scenario["ready_util"] = _peak(ready, "Utilization(10)")
    scenario["ready_sat"] = _peak(ready, "Saturation(10)")
    peaks = quse.groupby("queue-sys-id")["Utilization(10)"].max().sort_values(ascending=False)
    scenario["busiest"] = f"q{int(peaks.index[0])}" if not peaks.empty else ""
    span_ms = float(sysmetrics["time-step"].max()) if not sysmetrics.empty else float("nan")
    completed = scenario["matched"] + scenario["failed"]
    scenario["span_ms"] = span_ms
    scenario["throughput"] = (
        completed / (span_ms / 1000.0) if span_ms == span_ms and span_ms > 0 else float("nan")
    )


def write_summary(rows: list[dict], results: Path) -> None:
    results.mkdir(parents=True, exist_ok=True)
    first = rows[0]
    visits = expected_search_visits(READY_FAIL_P, MAX_READY_VISITS)
    search_mean = expected_search_mean()
    arrival = first["arrival_mean"]
    lam = 1.0 / arrival
    ready_rho = lam * visits * READY_WAIT
    lines = [
        "# Small matchmaking study",
        "",
        "Six stage queues (`q0` admit through `q5` allocate). "
        f"Each scenario offers {first['count']} tickets.",
        f"Unscaled search mean is ${search_mean:.2f}$ s and a ready retry brings the "
        f"expected search visits to ${visits:.2f}$.",
        f"The unscaled interarrival mean is ${arrival:.3f}$ s so a search pool of "
        f"{BASE_POOL} sits near $\\rho = {TARGET_RHO:.1f}$.",
        f"The same arrival puts the single ready worker near $\\rho = {ready_rho:.2f}$.",
        f"`--time-scale {first['time_scale']}` shortens every sleep, including the interarrival.",
        "Search and allocate are the pools under test. Every other stage has 1 worker.",
        f"Every buffer holds {first['qsize']} tickets, so a slower pool queues instead of dropping.",
        "`QUEUE-FULL` is a ticket that never entered a stage. "
        "A ready-check failure is a ticket that reached the failed sink.",
        "Time in the plots is milliseconds from the first event.",
        "",
        "| scenario | workers | matched | ready-check failures | QUEUE-FULL | mean latency (ms) | search peak util | search peak sat | ready peak util |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        latency = row["mean_latency"]
        latency_text = f"{latency:.1f}" if latency == latency else ""
        lines.append(
            f"| {row['name']} | {row['pool']} | {row['matched']} | {row['failed']} | "
            f"{row['queue_full']} | {latency_text} | {row['search_util']:.3f} | "
            f"{row['search_sat']:.1f} | {row['ready_util']:.3f} |"
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
            "The dark line on the USE panels is `q3` search. The other five queues are pale.",
            "Error and throughput charts start at zero.",
            "",
            "Each `results/<scenario>/tools/` directory holds the accessory figures",
            "(`use.png`, `resplot.png`, the response plots, `etrace.png`) and `q3-qmet.csv`.",
            "Logs and CSVs there are gitignored.",
            f"`etrace.png` is the first {TRACE_LIMIT} jobs. `longest-jobs.png` is the long-job trace.",
            "",
            "`sensitivity-servers.png` compares the seven pools on one chart: mean latency, "
            "`QUEUE-FULL` and ready-check failures as a percent of tickets, and tickets completed per second. "
            "`sensitivity-latency.png` is that latency series as bars. "
            "The sets are ordered from the smallest pool to the largest.",
            "",
        ]
    )
    (results / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    write_sensitivity(rows, results)


def write_sensitivity(rows: list[dict], results: Path) -> None:
    """Cross-scenario chart. Lab 0 drew latency, drops, and throughput on one figure."""
    ordered = sorted(rows, key=lambda row: (row["pool"], row["name"]))
    labels = [f"{row['name']}\n({row['pool']})" for row in ordered]
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
    figure.suptitle("Sensitivity: search and allocate workers", x=0.01, ha="left")
    figure.tight_layout()
    figure.savefig(results / "sensitivity-servers.png", dpi=120)
    plt.close(figure)

    figure, axis = plt.subplots(figsize=(8, 4.5))
    axis.bar(labels, latency, color="#2a78d6")
    axis.set_ylabel("mean end-to-end latency (ms)")
    axis.set_title("Server scaling, matchmaking small study")
    figure.tight_layout()
    figure.savefig(results / "sensitivity-latency.png", dpi=120)
    plt.close(figure)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the small matchmaking server sweep.")
    parser.add_argument("--only", default="", help="Comma-separated scenario names (default: all seven)")
    parser.add_argument("--count", type=int, default=TICKETS, help="Tickets per scenario (default: 1000)")
    parser.add_argument("--time-scale", type=float, default=TIME_SCALE)
    parser.add_argument("--arrival-mean", type=float, default=None, help="Unscaled interarrival mean. Default tunes baseline search rho to 0.8.")
    parser.add_argument("--runs", type=Path, default=HERE / "runs")
    parser.add_argument("--results", type=Path, default=HERE / "results")
    parser.add_argument("--plots-only", action="store_true", help="Plot an existing runs/<scenario> directory.")
    args = parser.parse_args(argv)
    if args.count < 1 or args.time_scale < 0:
        raise SystemExit("--count must be >= 1 and --time-scale must be >= 0")
    arrival = tuned_arrival_mean() if args.arrival_mean is None else args.arrival_mean
    if arrival <= 0:
        raise SystemExit("--arrival-mean must be > 0")
    wanted = {name.strip() for name in args.only.split(",") if name.strip()}
    chosen = [(index, name, pool) for index, (name, pool) in enumerate(SCENARIOS) if not wanted or name in wanted]
    if len(chosen) != (len(wanted) or len(SCENARIOS)):
        known = ", ".join(name for name, _pool in SCENARIOS)
        raise SystemExit(f"unknown scenario in --only. Known: {known}")
    print(
        f"tickets {args.count} time_scale {args.time_scale} arrival_mean {arrival:.4f}s "
        f"search_mean {expected_search_mean():.3f}s",
        flush=True,
    )
    rows = []
    for index, name, pool in chosen:
        print(f"scenario {name} search/allocate workers {pool}", flush=True)
        started = time.time()
        scenario = run_scenario(
            name,
            pool,
            index,
            args.runs,
            args.count,
            args.time_scale,
            arrival,
            plots_only=args.plots_only,
        )
        write_six_plots(scenario, args.results)
        print(
            f"  matched {scenario['matched']} failed {scenario['failed']} "
            f"queue-full {scenario['queue_full']} "
            f"latency {scenario['mean_latency']:.1f} ms "
            f"search util {scenario['search_util']:.3f} "
            f"({time.time() - started:.0f}s)",
            flush=True,
        )
        rows.append(scenario)
    write_summary(rows, args.results)
    print(f"summary: {args.results / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
