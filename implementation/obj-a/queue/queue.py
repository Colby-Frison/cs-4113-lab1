#!/usr/bin/env python3
"""Bounded job buffer between workload sources and the next edge.

Inputs are files/FIFOs (``--infile``) and, optionally, gRPC listen ports
(``--ingrpc``). Outputs are files/FIFOs (``--outfile``) and, optionally, gRPC
peers (``--outgrpc``). At least one input and one output are required. The two
kinds can be mixed on the same process.

How a run proceeds: the sidecar thread samples ``qsize`` every 0.1s. Outfiles
are opened before any producer starts, so a FIFO blocks in ``open`` until the
reader exists instead of filling the buffer first. File producers then parse
each data row and ``put_nowait``. ``queue.Full`` logs ``ERROR QUEUE-FULL`` and
drops that job; the producer does not block. File consumers and gRPC sender
threads compete on the same buffer. Each winner either appends ``raw_line`` or
calls ``Submit``. After file inputs hit EOF, a listen port keeps the process
up until Ctrl-C. The main thread then puts one ``None`` stop marker. Every
consumer requeues that marker and exits, so one marker wakes all of them.
``metric-q-size`` counts jobs still in the buffer. A job leaves that sample
when a consumer takes it, before the server function logs the visit.

Adapted from example_code/02_producer_and_consumer_instrumented.py.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from threading import Barrier, Thread

# The script's own directory is named queue/ and is on sys.path ahead of the
# standard library when this file is executed. Import the stdlib queue module
# before inserting the Objective A path.
_SCRIPT_DIR = Path(__file__).resolve().parent


def _load_stdlib_queue():
    cleaned: list[str] = []
    for entry in sys.path:
        candidate = Path.cwd() if entry == "" else Path(entry)
        try:
            if candidate.resolve() == _SCRIPT_DIR:
                continue
        except OSError:
            pass
        cleaned.append(entry)
    sys.path = cleaned
    import queue as stdlib_queue

    return stdlib_queue


_stdlib_queue = _load_stdlib_queue()
Full = _stdlib_queue.Full
Queue = _stdlib_queue.Queue

sys.path.insert(0, str(_SCRIPT_DIR.parent))

from lib.events import arrive_via, depart_via, error_queue_full
from lib.grpc_spec import DialSpec, ListenSpec, parse_dial, parse_listen
from lib.job import Job, JobParseError, is_header, parse_job_line
from lib.logsetup import configure_logging
from lib import metrics

logger = logging.getLogger(__name__)

# Yield so other threads, including the size sampler, can run. This is not service time.
YIELD_SEC = 0.001
SAMPLE_INTERVAL_SEC = 0.1


def admit_job(
    buffer: Queue[Job | None],
    job: Job,
    qid: str,
    source: str,
    log: logging.Logger,
    now: float | None = None,
) -> bool:
    """Queue ``job`` or drop it when the buffer is at its max size.

    Returns True when the job was accepted. ``now`` overrides the event
    timestamp so tests can check the line without reading the clock.
    """
    try:
        buffer.put_nowait(job)
    except Full:
        log.info("%s", error_queue_full(job.job_id, qid, now))
        metrics.record_drop(qid)
        return False
    log.info("%s", arrive_via(job.job_id, qid, source, now))
    metrics.record_admit(qid, job)
    return True


def worker_sidecar(
    buffer: Queue[Job | None],
    barrier: Barrier,
    sample_interval: float,
    qid: str = "q0",
) -> None:
    """Sample buffer occupancy until the main thread is waiting to shut down."""
    while True:
        depth = buffer.qsize()
        logger.info("metric-q-size %s", depth)
        metrics.set_queue_depth(qid, depth)
        time.sleep(sample_interval)
        if barrier.n_waiting > 0:
            break
    barrier.wait()


def worker_producer(
    barrier: Barrier,
    buffer: Queue[Job | None],
    identifier: int,
    filename: str,
    qid: str,
) -> None:
    """Read one infile. The main thread publishes the stop marker later."""
    logger.info("Producer %s running", identifier)
    try:
        with open(filename, encoding="utf-8") as handle:
            for line in handle:
                if not line.strip() or is_header(line):
                    continue
                try:
                    job = parse_job_line(line)
                except JobParseError as exc:
                    logger.error("skipping unreadable job line (%s): %s", exc, line.strip()[:200])
                    continue
                admit_job(buffer, job, qid, filename, logger)
                time.sleep(YIELD_SEC)
    finally:
        # Every producer reaches the barrier, including after a read error,
        # so a failed infile cannot leave the others waiting forever.
        barrier.wait()
    logger.info("Producer %s done", identifier)


def worker_grpc_sender(
    buffer: Queue[Job | None],
    dial: DialSpec,
    qid: str,
    submit,
) -> None:
    """Pull jobs and submit them to one gRPC peer until the stop marker."""
    logger.info("gRPC sender %s running", dial.address)
    while True:
        item = buffer.get()
        if item is None:
            buffer.put(item)
            break
        outcome = submit(dial, item, qid, logger=logger)
        if outcome != "failed":
            logger.info("%s", depart_via(item.job_id, qid, dial.address))
            metrics.record_depart(qid, item)
        else:
            metrics.record_buffer_take(qid, item)
        time.sleep(YIELD_SEC)
    logger.info("gRPC sender %s done", dial.address)


def worker_consumer(
    buffer: Queue[Job | None],
    identifier: int,
    handle,
    outfile: str,
    qid: str,
) -> None:
    """Forward jobs to one outfile until the shared stop marker arrives."""
    logger.info("Consumer %s running", identifier)
    while True:
        item = buffer.get()
        if item is None:
            buffer.put(item)
            break
        handle.write(item.raw_line + "\n")
        handle.flush()
        logger.info("%s", depart_via(item.job_id, qid, outfile))
        metrics.record_depart(qid, item)
        time.sleep(YIELD_SEC)
    logger.info("Consumer %s done", identifier)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="queue",
        description="Queue wait-task jobs from inputs to server-function outputs.",
    )
    parser.add_argument("-i", "--infile", action="append", help="Job CSV or FIFO. Repeatable. Optional if --ingrpc is set.")
    parser.add_argument("-o", "--outfile", action="append", help="Destination file or FIFO. Repeatable. Optional if --outgrpc is set.")
    parser.add_argument("--logfile", help="Log path (default: stderr)")
    parser.add_argument("--qid", default="q0", help="Queue id written in events (default: q0)")
    parser.add_argument(
        "--qsize",
        type=int,
        default=None,
        help="Max jobs waiting in this process. Omit for an unbounded buffer.",
    )
    parser.add_argument(
        "--ingrpc",
        action="append",
        help="Listen for jobs: interface:port,workers (example 0.0.0.0:50051,4)",
    )
    parser.add_argument(
        "--outgrpc",
        action="append",
        help="Send jobs: host:port,workers[,server|queue]. Default role is server.",
    )
    parser.add_argument(
        "--metrics-port",
        type=int,
        default=None,
        help="Expose Prometheus /metrics on this port. Omit to disable.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.qsize is not None and args.qsize < 1:
        raise SystemExit("--qsize must be >= 1")
    if not args.qid:
        raise SystemExit("--qid must be non-empty")

    input_files = list(args.infile or [])
    output_files = list(args.outfile or [])
    listens = [parse_listen(item) for item in (args.ingrpc or [])]
    dials = [parse_dial(item, caller="queue") for item in (args.outgrpc or [])]
    if not input_files and not listens:
        raise SystemExit("need at least one --infile or --ingrpc")
    if not output_files and not dials:
        raise SystemExit("need at least one --outfile or --outgrpc")

    configure_logging(args.logfile, "Q")
    logger.info("Started")
    logger.info("qid %s", args.qid)
    if args.metrics_port is not None:
        metrics.start_metrics(args.metrics_port)
        metrics.set_queue_capacity(args.qid, args.qsize)
        logger.info("metrics listen 0.0.0.0:%s", args.metrics_port)

    buffer: Queue[Job | None]
    if args.qsize is None:
        buffer = Queue()
    else:
        logger.info("max-q-size %s", args.qsize)
        buffer = Queue(maxsize=args.qsize)

    submit = None
    start_queue_server = None
    if listens or dials:
        try:
            from lib.grpc_runtime import start_queue_server as start_queue_server
            from lib.grpc_runtime import submit_job as submit
        except ImportError as exc:
            raise SystemExit(
                "gRPC edges need grpcio and protobuf. "
                "pip install -r implementation/obj-a/requirements.txt"
            ) from exc

    producer_barrier = Barrier(len(input_files)) if input_files else None
    sidecar_barrier = Barrier(2)
    sidecar = Thread(
        target=worker_sidecar,
        args=(buffer, sidecar_barrier),
        kwargs={"sample_interval": SAMPLE_INTERVAL_SEC, "qid": args.qid},
        daemon=True,
    )
    sidecar.start()

    # Open file outputs before producers run. A FIFO blocks here until the
    # reader exists, so a full buffer cannot drop jobs during that wait.
    with ExitStack() as stack:
        handles = [
            stack.enter_context(open(path, "a", encoding="utf-8", newline="\n"))
            for path in output_files
        ]
        consumers = [
            Thread(
                target=worker_consumer,
                args=(buffer, index, handles[index], output_files[index], args.qid),
            )
            for index in range(len(output_files))
        ]
        for dial in dials:
            for _ in range(dial.workers):
                consumers.append(Thread(target=worker_grpc_sender, args=(buffer, dial, args.qid, submit)))
        for consumer in consumers:
            consumer.start()

        servers = _start_listeners(listens, buffer, args.qid, start_queue_server)
        producers = []
        if producer_barrier is not None:
            producers = [
                Thread(
                    target=worker_producer,
                    args=(producer_barrier, buffer, index, input_files[index], args.qid),
                )
                for index in range(len(input_files))
            ]
            for producer in producers:
                producer.start()
        _wait_for_inputs(producers, servers)
        buffer.put(None)
        for consumer in consumers:
            consumer.join()

    sidecar_barrier.wait()
    sidecar.join()
    logger.info("Finished")
    return 0


def _start_listeners(listens: list[ListenSpec], buffer: Queue[Job | None], qid: str, start_queue_server):
    servers = []
    if not listens:
        return servers

    def admit(job: Job, source: str) -> bool:
        return admit_job(buffer, job, qid, source, logger)

    for listen in listens:
        server, port = start_queue_server(listen, admit)
        logger.info("gRPC listen %s:%s workers %s", listen.host, port, listen.workers)
        servers.append(server)
    return servers


def _wait_for_inputs(producers: list[Thread], servers: list) -> None:
    """Wait until file inputs hit EOF, and until Ctrl-C if a gRPC port is open."""
    try:
        for producer in producers:
            producer.join()
        if not servers:
            return
        logger.info("gRPC listen is running; Ctrl-C to stop")
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        logger.info("stopping gRPC listen")
    finally:
        for server in servers:
            server.stop(1)


if __name__ == "__main__":
    raise SystemExit(main())
