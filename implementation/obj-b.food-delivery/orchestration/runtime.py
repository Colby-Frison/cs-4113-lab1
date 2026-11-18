"""Container entry points for the two-queue food-delivery application."""

import argparse
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import threading
import time

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from fd.analysis import analyze
from fd.dispatch import Dispatcher, file_logger
from fd.server import serve
from fd.workload import generate
from lib.job import is_header, parse_job_line
from run_demo import validate_config

ROOT = Path(os.environ.get("FOOD_ROOT", "/data/run"))
IPC = Path(os.environ.get("FOOD_IPC", "/ipc"))
CONFIG = json.loads(os.environ.get("FOOD_CONFIG", "{}"))
TRANSPORT = os.environ.get("FOOD_TRANSPORT", "grpc")
DEADLINE = time.monotonic() + float(os.environ.get("FOOD_TIMEOUT", "120"))


def workers():
    return [f"kitchen-{i}" for i in range(CONFIG["kitchen_workers"])] + [
        f"courier-{i}" for i in range(CONFIG["couriers"])
    ]


def address(name):
    """Use service DNS in Compose and distinct localhost ports within the Kubernetes Pod."""
    port = 50050 + ["q0", "q1", *workers()].index(name)
    host = "127.0.0.1" if os.environ.get("FOOD_LOCALHOST") == "1" else name
    return f"{host}:{port}"


def check():
    failures = list(ROOT.glob("*.failed"))
    if failures:
        raise RuntimeError(failures[0].read_text())
    if time.monotonic() >= DEADLINE:
        raise TimeoutError("food-delivery run deadline exceeded")


class Pipe:
    """Exchange newline-delimited CSV rows through a shared named pipe."""

    def __init__(self, name):
        # RDWR avoids EOF between writers, while nonblocking I/O keeps deadlines usable
        self.fd = os.open(IPC / name, os.O_RDWR | os.O_NONBLOCK)
        self.pending = b""

    def close(self):
        os.close(self.fd)

    def send(self, row):
        data = (row.rstrip("\n") + "\n").encode()
        # one atomic write prevents concurrent kitchen handoffs from interleaving
        if len(data) > os.fpathconf(self.fd, "PC_PIPE_BUF"):
            raise ValueError("order exceeds the FIFO atomic write limit")
        while True:
            check()
            try:
                os.write(self.fd, data)
                return
            except BlockingIOError:
                select.select([], [self.fd], [], 0.05)

    def receive(self):
        while b"\n" not in self.pending:
            check()
            if (ROOT / "done").exists():
                return None
            if select.select([self.fd], [], [], 0.05)[0]:
                self.pending += os.read(self.fd, 65536)
        row, self.pending = self.pending.split(b"\n", 1)
        return row.decode()


def initialize():
    validate_config(CONFIG)
    ROOT.mkdir(parents=True, exist_ok=True)
    if any(ROOT.iterdir()):
        raise ValueError(f"{ROOT} is not empty; use a fresh results directory")
    IPC.mkdir(parents=True, exist_ok=True)
    (ROOT / "config.json").write_text(json.dumps({**CONFIG, "transport": TRANSPORT}, indent=2))
    if TRANSPORT == "fifo":
        for name in [
            "q0",
            "q1",
            *[f"{worker}-{edge}" for worker in workers() for edge in ("in", "out")],
        ]:
            os.mkfifo(IPC / name)


def grpc_request(job, source):
    from lib.proto_gen import job_pb2

    return job_pb2.SubmitJobRequest(source_edge=source, job=job_pb2.Job(raw_line=job.raw_line))


def queue_service(qid):
    """Reuse the stateful dispatcher, with service work performed by separate containers."""
    count = CONFIG["kitchen_workers" if qid == "q0" else "couriers"]
    stage = "kitchen" if qid == "q0" else "courier"
    done = threading.Event()
    with ExitStack() as resources:
        if TRANSPORT == "grpc":
            import grpc
            from lib.grpc_runtime import start_queue_server
            from lib.grpc_spec import ListenSpec
            from lib.proto_gen import queue_to_server_pb2_grpc

            stubs = []
            for index in range(count):
                channel = grpc.insecure_channel(address(f"{stage}-{index}"))
                resources.callback(channel.close)
                stubs.append(queue_to_server_pb2_grpc.QueueToServerStub(channel))

            def delivery(job, index):
                response = stubs[index].Submit(grpc_request(job, qid), timeout=60)
                if not response.accepted:
                    raise RuntimeError("server refused service")

        else:

            def pipe(name):
                connection = Pipe(name)
                resources.callback(connection.close)
                return connection

            incoming = pipe(qid)
            requests = [pipe(f"{stage}-{i}-in") for i in range(count)]
            replies = [pipe(f"{stage}-{i}-out") for i in range(count)]
            downstream = pipe("q1") if qid == "q0" else None

            def delivery(job, index):
                requests[index].send(job.raw_line)
                if replies[index].receive() != job.raw_line:
                    raise RuntimeError("server reply changed or lost an order")
                if downstream:
                    downstream.send(job.raw_line)

        dispatcher = Dispatcher(
            count,
            CONFIG["kitchen_capacity" if qid == "q0" else "pickup_capacity"],
            ROOT / qid,
            qid=qid,
            delivery=delivery,
        )
        if TRANSPORT == "grpc":
            port = int(address(qid).split(":")[1])
            server, _ = start_queue_server(ListenSpec("0.0.0.0", port, 4), dispatcher.admit)
            resources.callback(lambda: server.stop(0).wait())
        else:

            def read_orders():
                try:
                    while (row := incoming.receive()) is not None:
                        dispatcher.admit(parse_job_line(row), "orders" if qid == "q0" else "q0")
                except Exception as exc:
                    dispatcher.fail(exc)

            threading.Thread(target=read_orders, daemon=True).start()

        def await_completion():
            try:
                while not (ROOT / "done").exists():
                    check()
                    time.sleep(0.05)
            except Exception as exc:
                dispatcher.fail(exc)
            finally:
                done.set()

        threading.Thread(target=await_completion, daemon=True).start()
        (ROOT / f"{qid}.ready").touch()
        dispatcher.run(input_done=done)


def server_service(name):
    """Run an existing Python FIFO service or the existing C++ gRPC server."""
    stage, worker = name.split("-")
    qid = "q0" if stage == "kitchen" else "q1"
    sink = ROOT / f"sink-{worker}.csv"
    if TRANSPORT == "grpc":
        args = [
            str(APP / "cpp/build/food_server"),
            "--stage",
            stage,
            "--qid",
            qid,
            "--server-id",
            worker,
            "--logfile",
            str(ROOT / f"{name}.log"),
            "--ready-file",
            str(ROOT / f"{name}.ready"),
            "--listen",
            "0.0.0.0:" + address(name).split(":")[1],
        ]
        args += ["--outgrpc", address("q1")] if stage == "kitchen" else ["--out", str(sink)]
        process = subprocess.Popen(args)
        try:
            while not (ROOT / "done").exists():
                check()
                if process.poll() is not None:
                    raise RuntimeError(f"{name} exited unexpectedly: {process.returncode}")
                time.sleep(0.05)
        finally:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    else:
        incoming, reply = Pipe(f"{name}-in"), Pipe(f"{name}-out")
        log = file_logger(ROOT / f"{name}.log", "sv")
        try:
            with sink.open("w") if stage == "courier" else io.StringIO() as output:
                (ROOT / f"{name}.ready").touch()
                while (row := incoming.receive()) is not None:
                    serve(
                        parse_job_line(row),
                        stage,
                        qid,
                        int(worker),
                        output,
                        "q1" if stage == "kitchen" else str(sink),
                        log,
                    )
                    # acknowledge only after service, output, and completion logging finish
                    reply.send(row)
                    if stage == "kitchen":
                        output.seek(0)
                        output.truncate()
        finally:
            incoming.close()
            reply.close()
            for handler in log.handlers:
                handler.close()


def producer():
    """Generate a finite workload, wait for complete accounting, then run Objective A analysis."""
    for name in ["q0", "q1", *workers()]:
        while not (ROOT / f"{name}.ready").exists():
            check()
            time.sleep(0.05)
    with ExitStack() as resources:
        if TRANSPORT == "grpc":
            import grpc
            from lib.proto_gen import queue_to_queue_pb2_grpc

            channel = grpc.insecure_channel(address("q0"))
            resources.callback(channel.close)
            grpc.channel_ready_future(channel).result(timeout=30)
            stub = queue_to_queue_pb2_grpc.QueueToQueueStub(channel)
            send = lambda row: stub.Submit(grpc_request(parse_job_line(row), "orders"), timeout=30)
        else:
            pipe = Pipe("q0")
            resources.callback(pipe.close)
            send = pipe.send
        source = resources.enter_context((ROOT / "jobs.csv").open("w"))

        class Output:
            def write(self, text):
                source.write(text)
                if text.strip() and not is_header(text):
                    check()
                    send(text.strip())

            def flush(self):
                source.flush()

        generate(
            Output(),
            **{key: CONFIG[key] for key in ("count", "seed", "arrival_mean", "time_scale")},
        )
        while True:
            check()
            delivered = sum(len(path.read_text().splitlines()) for path in ROOT.glob("sink-*.csv"))
            dropped = sum(
                path.read_text().count("ERROR QUEUE-FULL") for path in ROOT.glob("q*/q*.log")
            )
            states = [
                json.loads((ROOT / qid / "dispatch-state.json").read_text()) for qid in ("q0", "q1")
            ]
            idle = all(
                not state["waiting"]
                and all(worker["status"] == "available" for worker in state["couriers"].values())
                for state in states
            )
            if delivered + dropped >= CONFIG["count"] and idle:
                break
            time.sleep(0.05)
    for qid in ("q0", "q1"):
        shutil.copyfile(ROOT / qid / f"{qid}.log", ROOT / f"{qid}.log")
    shutil.copyfile(ROOT / "q1/dispatch-state.json", ROOT / "dispatch-state.json")
    result = analyze(ROOT, CONFIG, "stages")
    print(json.dumps(result, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("init", "queue", "server", "generator"))
    parser.add_argument("--name")
    args = parser.parse_args()
    name = args.name or args.role
    try:
        if args.role == "init":
            initialize()
        elif args.role == "queue":
            queue_service(name)
        elif args.role == "server":
            server_service(name)
        else:
            producer()
    except Exception as exc:
        if args.role != "init":
            (ROOT / f"{name}.failed").write_text(f"{name}: {exc}\n")
        raise
    finally:
        if args.role == "generator":
            (ROOT / "done").touch()


if __name__ == "__main__":
    main()
