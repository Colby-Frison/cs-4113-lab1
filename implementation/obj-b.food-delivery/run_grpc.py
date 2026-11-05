#!/usr/bin/env python3
"""Python workload/queues/dispatch with C++ server functions over loopback gRPC."""
import argparse
from collections import deque
from concurrent import futures
from contextlib import ExitStack
from datetime import datetime
import json
from pathlib import Path
import subprocess
import threading
import time

from fd import APP
from fd.analysis import analyze
from fd.dispatch import Dispatcher, file_logger
from fd.workload import generate, nonnegative, positive_int
from lib.events import arrive_via, error_queue_full
from lib.job import HEADER, parse_job_line
from run_demo import validate_config


def run(root, config, mode="stages", *, binary=None, timeout=60, plots=False):
    # Import only here so the ordinary Python/FIFO application stays dependency-free.
    import grpc
    from lib.proto_gen import job_pb2, queue_to_server_pb2_grpc, server_to_queue_pb2_grpc

    validate_config(config)
    if mode not in ("stages", "monolith") or not 0 < timeout < float("inf"):
        raise ValueError("invalid mode or timeout")
    binary = Path(binary or APP / "cpp/build/food_server").resolve()
    if not binary.is_file():
        raise ValueError(f"build the C++ server first: {binary}")
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError(f"refusing to overwrite nonempty run directory: {root}")
    (root / "config.json").write_text(json.dumps({**config, "mode": mode, "transport": "grpc", "server_language": "cpp"}, indent=2) + "\n")
    deadline = time.monotonic() + timeout
    processes, channels, senders = [], [], []
    ingress = dispatch = dispatch_thread = None
    input_done = threading.Event()
    condition = threading.Condition()
    pending = deque()
    source_done = False
    failures = []
    qlog = file_logger(root / "q0.log", "Q")

    def remaining():
        if failures:
            raise RuntimeError(f"gRPC pipeline failed: {failures[0]}")
        if dispatch and dispatch.failure:
            raise RuntimeError(f"dispatch failed: {dispatch.failure}")
        for name, process in processes:
            if process.poll() is not None:
                raise RuntimeError(f"{name} exited; inspect {name}.stderr")
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError(f"gRPC run timed out after {timeout}s")
        return left

    def paced_sleep(seconds):
        end = time.monotonic() + seconds
        while True:
            remaining()
            delay = end - time.monotonic()
            if delay <= 0:
                return
            time.sleep(min(delay, 0.05))

    def visit(stub, job, source):
        request = job_pb2.SubmitJobRequest(source_edge=source, job=job_pb2.Job(
            job_id=job.job_id, arrival_time=job.arrival_time, raw_line=job.raw_line,
            tasks=[job_pb2.Task(tid=t.tid, wait_seconds=t.wait_seconds) for t in job.tasks]))
        response = stub.Submit(request, timeout=remaining())
        if not response.accepted:
            raise RuntimeError(f"server rejected {job.job_id}")

    try:
        with ExitStack() as files:
            def launch(stage, index, peer=None):
                name = f"{stage}-{index}"
                ready = root / f"{name}.ready"
                args = [str(binary), "--stage", stage, "--qid", "q1" if stage == "courier" else "q0",
                        "--server-id", str(index), "--logfile", str(root / f"{name}.log"),
                        "--ready-file", str(ready), "--listen", "127.0.0.1:0"]
                args += ["--outgrpc", peer] if peer else ["--out", str(root / f"sink-{index}.csv")]
                err = files.enter_context((root / f"{name}.stderr").open("w"))
                process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=err)
                processes.append((name, process))
                while not ready.exists() or not ready.read_text().strip():
                    remaining()
                    time.sleep(0.01)
                port = int(ready.read_text())
                channel = grpc.insecure_channel(f"127.0.0.1:{port}", options=[("grpc.enable_retries", 0)])
                channels.append(channel)
                grpc.channel_ready_future(channel).result(timeout=remaining())
                return queue_to_server_pb2_grpc.QueueToServerStub(channel)

            peer = None
            if mode == "stages":
                couriers = [launch("courier", i) for i in range(config["couriers"])]
                dispatch = Dispatcher(config["couriers"], config["pickup_capacity"], root,
                                      delivery=lambda job, index: visit(couriers[index], job, "q1"))

                class Pickup(server_to_queue_pb2_grpc.ServerToQueueServicer):
                    def Submit(self, request, context):
                        try:
                            # The C++ kitchen sends the validated, original workload row.
                            job = parse_job_line(request.job.raw_line)
                            with dispatch.condition:
                                if dispatch.stopping:
                                    context.abort(grpc.StatusCode.UNAVAILABLE, "dispatcher stopped")
                            accepted = dispatch.admit(job, request.source_edge or "q0")
                            return job_pb2.SubmitJobResponse(accepted=accepted)
                        except ValueError as exc:
                            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
                        except grpc.RpcError:
                            raise
                        except Exception as exc:
                            dispatch.fail(exc)
                            context.abort(grpc.StatusCode.INTERNAL, str(exc))

                ingress = grpc.server(futures.ThreadPoolExecutor(max_workers=config["kitchen_workers"]))
                server_to_queue_pb2_grpc.add_ServerToQueueServicer_to_server(Pickup(), ingress)
                port = ingress.add_insecure_port("127.0.0.1:0")
                if not port:
                    raise RuntimeError("cannot bind pickup queue")
                ingress.start()
                peer = f"127.0.0.1:{port}"

                def dispatch_run():
                    try:
                        dispatch.run(input_done=input_done)
                    except Exception as exc:
                        failures.append(exc)
                dispatch_thread = threading.Thread(target=dispatch_run)
                dispatch_thread.start()

            workers = [launch("kitchen" if mode == "stages" else "monolith", i, peer)
                       for i in range(config["kitchen_workers"])]

            def sender(stub):
                try:
                    while True:
                        with condition:
                            condition.wait_for(lambda: pending or source_done or failures)
                            if failures:
                                return
                            if not pending:
                                return
                            job = pending.popleft()
                        visit(stub, job, "q0")
                except Exception as exc:
                    with condition:
                        failures.append(exc)
                        condition.notify_all()

            senders = [threading.Thread(target=sender, args=(stub,)) for stub in workers]
            for thread in senders:
                thread.start()
            record = files.enter_context((root / "jobs.csv").open("w"))

            class WorkloadOutput:
                def write(self, row):
                    remaining()
                    record.write(row)
                    if row.strip() == HEADER:
                        return
                    job = parse_job_line(row)
                    with condition:
                        if len(pending) >= config["kitchen_capacity"]:
                            qlog.info("%s", error_queue_full(job.job_id, "q0"))
                        else:
                            # Admission log precedes any worker's service entry.
                            qlog.info("%s", arrive_via(job.job_id, "q0", "orders"))
                            pending.append(job)
                            condition.notify()
                def flush(self):
                    record.flush()

            generate(WorkloadOutput(), count=config["count"], seed=config["seed"],
                     arrival_mean=config["arrival_mean"], time_scale=config["time_scale"], sleep=paced_sleep)
            with condition:
                source_done = True
                condition.notify_all()
            for thread in senders:
                while thread.is_alive():
                    thread.join(min(0.05, remaining()))
            remaining()
            # All kitchen RPCs have returned: no further q1 admissions can arrive.
            input_done.set()
            if dispatch_thread:
                while dispatch_thread.is_alive():
                    dispatch_thread.join(min(0.05, remaining()))
            remaining()
    finally:
        with condition:
            source_done = True
            pending.clear()
            condition.notify_all()
        if dispatch:
            with dispatch.condition:
                dispatch.stopping = True
                dispatch.condition.notify_all()
        input_done.set()
        if ingress:
            ingress.stop(0).wait()
        # Closing channels cancels in-flight calls on failure; no automatic retries.
        for channel in channels:
            channel.close()
        for _, process in processes:
            if process.poll() is None:
                process.terminate()
        for _, process in processes:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for thread in senders:
            thread.join(timeout=2)
        if dispatch_thread:
            dispatch_thread.join(timeout=2)
        for handler in qlog.handlers:
            handler.close()
    return analyze(root, config, mode, plots=plots)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=APP / "topology/baseline.json")
    parser.add_argument("--mode", choices=("stages", "monolith"), default="stages")
    parser.add_argument("--binary", type=Path, default=APP / "cpp/build/food_server")
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--count", type=positive_int)
    parser.add_argument("--arrival-mean", type=nonnegative)
    parser.add_argument("--time-scale", type=nonnegative)
    parser.add_argument("--timeout", type=nonnegative, default=60)
    parser.add_argument("--plots", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    for key in ("count", "arrival_mean", "time_scale"):
        if getattr(args, key) is not None:
            config[key] = getattr(args, key)
    root = args.workdir or APP / "runs" / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-cpp")
    try:
        summary = run(root, config, args.mode, binary=args.binary, timeout=args.timeout, plots=args.plots)
    except (ImportError, ValueError, RuntimeError, TimeoutError) as exc:
        parser.exit(1, f"{exc}\n")
    print(json.dumps(summary, indent=2))
    print(f"Artifacts: {root.resolve()}")


if __name__ == "__main__":
    main()
