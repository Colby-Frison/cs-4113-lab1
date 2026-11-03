#!/usr/bin/env python3
"""Run live FIFO stages on macOS/Linux, using the unchanged Objective A queues."""
import argparse
from contextlib import ExitStack
from datetime import datetime
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from fd import APP, OBJ_A
from fd.analysis import analyze
from fd.workload import nonnegative, positive_int


def validate_config(config):
    expected = {"kitchen_workers", "couriers", "kitchen_capacity", "pickup_capacity", "count",
                "arrival_mean", "time_scale", "seed"}
    if set(config) != expected:
        raise ValueError(f"config keys must be {sorted(expected)}")
    for key in expected - {"arrival_mean", "time_scale", "seed"}:
        if type(config[key]) is not int or config[key] < 1:
            raise ValueError(f"{key} must be an integer >= 1")
    if type(config["seed"]) is not int:
        raise ValueError("seed must be an integer")
    for key in ("arrival_mean", "time_scale"):
        value = config[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{key} must be finite and >= 0")


def run(root, config, mode="stages", timeout=60, plots=False):
    validate_config(config)
    
    if not hasattr(os, "mkfifo"):
        raise ValueError("live demo requires macOS/Linux or WSL (os.mkfifo)")
    
    if mode not in ("stages", "monolith") or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("invalid mode or timeout")
    
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError(f"refusing to overwrite nonempty run directory: {root}")
    
    (root / "config.json").write_text(json.dumps({**config, "mode": mode}, indent=2) + "\n")
    processes = []
    with tempfile.TemporaryDirectory(prefix="food-delivery-") as tmp, ExitStack() as stack:
        pipes = Path(tmp)
        def fifo(name):
            path = pipes / name
            os.mkfifo(path)
            return path

        def launch(name, script, args):
            stderr = stack.enter_context((root / f"{name}.stderr").open("w"))
            process = subprocess.Popen([sys.executable, str(script), *map(str, args)],
                                       stdout=subprocess.DEVNULL, stderr=stderr,
                                       env={**os.environ, "LAB_LOG_COMPACT": "1"})
            processes.append((name, process))

        def server(name, stage, qid, sid, source, dest):
            script = APP / ("server-monolith" if stage == "monolith" else "server-stages") / "server_func.py"
            args = ["-i", source, "-o", dest, "--qid", qid, "--server-id", sid,
                    "--logfile", root / f"{name}.log"]
            if stage != "monolith":
                args += ["--stage", stage]
            launch(name, script, args)

        def queue(qid, capacity, sources, destinations):
            args = ["--qid", qid, "--qsize", capacity, "--logfile", root / f"{qid}.log"]
            for source in sources:
                args += ["-i", source]
            for dest in destinations:
                args += ["-o", dest]
            launch(qid, OBJ_A / "queue/queue.py", args)

        try:
            incoming = fifo("orders")
            kitchen_inputs = [fifo(f"kitchen-{i}") for i in range(config["kitchen_workers"])]
            if mode == "stages":
                ready = [fifo(f"prepared-{i}") for i in range(config["kitchen_workers"])]
                dispatch_args = ["--couriers", config["couriers"], "--capacity", config["pickup_capacity"],
                                 "--workdir", root]
                for source in ready:
                    dispatch_args += ["--infile", source]
                launch("dispatcher", APP / "dispatcher/dispatcher.py", dispatch_args)
                for i, source in enumerate(kitchen_inputs):
                    server(f"kitchen-{i}", "kitchen", "q0", i, source, ready[i])
            else:
                for i, source in enumerate(kitchen_inputs):
                    server(f"monolith-{i}", "monolith", "q0", i, source, root / f"sink-{i}.csv")
            queue("q0", config["kitchen_capacity"], [incoming], kitchen_inputs)
            launch("generator", APP / "workload-generator/workload_generator.py", [
                "--out", incoming, "--record", root / "jobs.csv", "--count", config["count"],
                "--seed", config["seed"], "--arrival-mean", config["arrival_mean"],
                "--time-scale", config["time_scale"]])
            deadline = time.monotonic() + timeout
            while True:
                failed = [(name, p.returncode) for name, p in processes if p.poll() not in (None, 0)]
                if failed:
                    raise RuntimeError(f"child process failed: {failed}; inspect *.stderr in {root}")
                if all(p.poll() == 0 for _, p in processes):
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"run timed out after {timeout}s; logs preserved in {root}")
                time.sleep(0.02)
        finally:
            for _, process in processes:
                if process.poll() is None:
                    process.terminate()
            for _, process in processes:
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    return analyze(root, config, mode, plots=plots)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=APP / "topology/baseline.json")
    parser.add_argument("--mode", choices=("stages", "monolith"), default="stages")
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--count", type=positive_int)
    parser.add_argument("--arrival-mean", type=nonnegative)
    parser.add_argument("--time-scale", type=nonnegative)
    parser.add_argument("--timeout", type=nonnegative, default=60)
    parser.add_argument("--plots", action="store_true", help="Run Objective A plotting tools (numpy/pandas/matplotlib)")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    for name in ("count", "arrival_mean", "time_scale"):
        if getattr(args, name) is not None:
            config[name] = getattr(args, name)
    root = args.workdir or APP / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    try:
        summary = run(root, config, args.mode, args.timeout, args.plots)
    except (ValueError, RuntimeError, TimeoutError) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(summary, indent=2))
    print(f"Artifacts: {root.resolve()}")


if __name__ == "__main__":
    main()
