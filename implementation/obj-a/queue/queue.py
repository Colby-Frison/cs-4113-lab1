#!/usr/bin/env python3
"""Bounded job buffer between workload files (or FIFOs) and server functions.

Producers read wait-task CSV rows. A job that fits is logged ``ARRIVE-VIA``
and queued. A job that does not fit is logged ``ERROR QUEUE-FULL`` and
dropped; the producer does not block. Consumers write the original line to an
outfile and log ``DEPART-VIA``. One consumer thread competes per outfile, the
same arrangement as the course example.

``metric-q-size`` is the number of jobs sitting in this buffer. A job leaves
the sample once a consumer takes it, so the sample is not in-service time.
The server function logs the visit.

Adapted from example_code/02_producer_and_consumer_instrumented.py.
gRPC flags are accepted only so a mistaken command fails clearly.
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
from lib.job import Job, JobParseError, is_header, parse_job_line
from lib.logsetup import configure_logging, reject_unimplemented_grpc

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
        return False
    log.info("%s", arrive_via(job.job_id, qid, source, now))
    return True


def worker_sidecar(buffer: Queue[Job | None], barrier: Barrier, sample_interval: float) -> None:
    """Sample buffer occupancy until the main thread is waiting to shut down."""
    while True:
        logger.info("metric-q-size %s", buffer.qsize())
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
    """Read one infile. The first producer publishes the stop marker."""
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
        # so a failed infile cannot leave the consumers waiting forever.
        barrier.wait()
        if identifier == 0:
            buffer.put(None)
    logger.info("Producer %s done", identifier)


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
        time.sleep(YIELD_SEC)
    logger.info("Consumer %s done", identifier)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="queue",
        description="Queue wait-task jobs from inputs to server-function outputs.",
    )
    parser.add_argument("-i", "--infile", action="append", required=True)
    parser.add_argument("-o", "--outfile", action="append", required=True)
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
        help="Not implemented. interface,num_workers",
    )
    parser.add_argument(
        "--outgrpc",
        action="append",
        help="Not implemented. host:port,num_workers",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    reject_unimplemented_grpc(args.ingrpc, args.outgrpc)
    if args.qsize is not None and args.qsize < 1:
        raise SystemExit("--qsize must be >= 1")
    if not args.qid:
        raise SystemExit("--qid must be non-empty")

    configure_logging(args.logfile, "Q")
    logger.info("Started")
    logger.info("qid %s", args.qid)

    buffer: Queue[Job | None]
    if args.qsize is None:
        buffer = Queue()
    else:
        logger.info("max-q-size %s", args.qsize)
        buffer = Queue(maxsize=args.qsize)

    input_files: list[str] = args.infile
    output_files: list[str] = args.outfile
    producer_barrier = Barrier(len(input_files))
    sidecar_barrier = Barrier(2)

    sidecar = Thread(
        target=worker_sidecar,
        args=(buffer, sidecar_barrier),
        kwargs={"sample_interval": SAMPLE_INTERVAL_SEC},
        daemon=True,
    )
    sidecar.start()

    # Open outputs before producers run. A FIFO blocks here until the server
    # function is already reading, so a full buffer cannot drop jobs during
    # that wait.
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
        for consumer in consumers:
            consumer.start()

        producers = [
            Thread(
                target=worker_producer,
                args=(producer_barrier, buffer, index, input_files[index], args.qid),
            )
            for index in range(len(input_files))
        ]
        for producer in producers:
            producer.start()
        for producer in producers:
            producer.join()
        for consumer in consumers:
            consumer.join()

    sidecar_barrier.wait()
    sidecar.join()
    logger.info("Finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
