"""Wait-task visit shared by the stateless and stateful server functions.

``serve_job`` is one visit. It logs ``ENTERS-SERVER``, then for each task logs
``TASK-START``, calls the sleep function, and logs ``TASK-END``, then logs
``EXITS-SERVER``. It then picks one outgoing edge uniformly. A file edge
appends ``raw_line`` and flushes. A gRPC edge calls ``Submit`` and logs
``DEPART-VIA`` when the peer answers, including a rejection. A transport
failure logs an error and does not log ``DEPART-VIA``. No edges at all logs
``ERROR NO-OUT-EDGE``.

``serve_stream`` is the file path: open every outfile up front, skip the CSV
header, and call ``serve_job`` per row on this thread. ``run_cli`` uses that
for ``--infile``. For ``--ingrpc`` it binds a server whose handler calls
``serve_job`` and blocks until Ctrl-C. Optional ``state`` counts visits per
job id inside this process only. The count is locked when the gRPC pool can
run several visits at once. It is not written on the outgoing edge.
"""

from __future__ import annotations

import argparse
import logging
import random
import threading
import time
from contextlib import ExitStack
from typing import Callable, TextIO

from lib.events import (
    depart_via,
    enters_server,
    error_no_out_edge,
    exits_server,
    task_end,
    task_start,
)
from lib.grpc_spec import DialSpec, parse_dial, parse_listen
from lib.job import Job, JobParseError, is_header, parse_job_line
from lib.logsetup import configure_logging

SleepFn = Callable[[float], None]
SubmitFn = Callable[[DialSpec, Job, str], str]


def serve_job(
    job: Job,
    outfiles: list[str],
    qid: str,
    server_id: int,
    logger: logging.Logger,
    *,
    state: dict[str, int] | None = None,
    sleep_fn: SleepFn = time.sleep,
    rng: random.Random | None = None,
    writers: list[TextIO] | None = None,
    grpc_edges: list[DialSpec] | None = None,
    submit_fn: SubmitFn | None = None,
    state_lock: threading.Lock | None = None,
) -> str | None:
    """Run one visit. Return the edge label used, or None when the job stays here.

    File edges and gRPC edges are one pool. The visit picks one at random.
    ``writers``, when passed, must match ``outfiles``; the caller owns them.
    ``state_lock`` guards the visit count when several gRPC workers run at once.
    """
    chooser = rng or random.Random()
    logger.info("%s", enters_server(job.job_id, qid, server_id))
    if state is not None:
        _count_visit(state, job.job_id, state_lock)

    for task in job.tasks:
        logger.info("%s", task_start(job.job_id, qid, task.tid, task.wait_seconds))
        sleep_fn(task.wait_seconds)
        logger.info("%s", task_end(job.job_id, qid, task.tid, task.wait_seconds))

    logger.info("%s", exits_server(job.job_id, qid, server_id))
    return _forward(job, outfiles, writers, grpc_edges or [], qid, logger, chooser, submit_fn)


def serve_stream(
    infile: str,
    outfiles: list[str],
    qid: str,
    server_id: int,
    logger: logging.Logger,
    *,
    state: dict[str, int] | None = None,
    sleep_fn: SleepFn = time.sleep,
    rng: random.Random | None = None,
    grpc_edges: list[DialSpec] | None = None,
    submit_fn: SubmitFn | None = None,
) -> None:
    """Read ``infile`` until EOF, sleeping through every wait-task job."""
    chooser = rng or random.Random()
    edges = list(grpc_edges or [])
    with open(infile, encoding="utf-8") as incoming, ExitStack() as stack:
        handles = [
            stack.enter_context(open(path, "a", encoding="utf-8", newline="\n"))
            for path in outfiles
        ]
        for line in incoming:
            if not line.strip() or is_header(line):
                continue
            try:
                job = parse_job_line(line)
            except JobParseError as exc:
                logger.error("skipping unreadable job line (%s): %s", exc, _preview(line))
                continue
            serve_job(
                job,
                outfiles,
                qid,
                server_id,
                logger,
                state=state,
                sleep_fn=sleep_fn,
                rng=chooser,
                writers=handles,
                grpc_edges=edges,
                submit_fn=submit_fn,
            )


def _write_job(path: str, raw_line: str, handle: TextIO | None) -> None:
    if handle is None:
        with open(path, "a", encoding="utf-8", newline="\n") as opened:
            opened.write(raw_line + "\n")
            opened.flush()
        return
    handle.write(raw_line + "\n")
    handle.flush()


def build_parser(stateful: bool) -> argparse.ArgumentParser:
    """CLI shared by the stateless and stateful server-function scripts."""
    if stateful:
        description = (
            "Stateful wait-task server. Counts visits per job id in this process, "
            "sleeps each wait, and forwards the original job line."
        )
    else:
        description = (
            "Stateless wait-task server. Sleeps each wait and forwards the original job line."
        )
    parser = argparse.ArgumentParser(prog="server-func", description=description)
    parser.add_argument(
        "-i",
        "--infile",
        help="Job CSV or FIFO to read. Omit when --ingrpc is set.",
    )
    parser.add_argument(
        "-o",
        "--outfile",
        action="append",
        help="Destination file or FIFO. Repeat for several edges. Can be mixed with --outgrpc.",
    )
    parser.add_argument("--logfile", help="Log path (default: stderr)")
    parser.add_argument("--qid", default="q0", help="Queue id written in events (default: q0)")
    parser.add_argument(
        "--server-id",
        type=int,
        default=0,
        help="Server index written in ENTERS-SERVER / EXITS-SERVER (default: 0)",
    )
    parser.add_argument(
        "--ingrpc",
        help="Listen for queue-to-server jobs: interface:port,workers (example 0.0.0.0:50052,2)",
    )
    parser.add_argument(
        "--outgrpc",
        action="append",
        help="Forward to a queue: host:port,workers (server-to-queue). Repeat for several queues.",
    )
    return parser


def run_cli(argv: list[str] | None = None, *, stateful: bool) -> int:
    """Serve jobs from a file/FIFO or from a gRPC listen port."""
    args = build_parser(stateful).parse_args(argv)
    if args.server_id < 0:
        raise SystemExit("--server-id must be >= 0")
    if not args.qid:
        raise SystemExit("--qid must be non-empty")
    if args.infile and args.ingrpc:
        raise SystemExit("pass either --infile or --ingrpc, not both")
    if not args.infile and not args.ingrpc:
        raise SystemExit("need --infile or --ingrpc")

    dials = [parse_dial(item, caller="server") for item in (args.outgrpc or [])]
    outfiles = list(args.outfile or [])
    if dials or args.ingrpc:
        try:
            import lib.grpc_runtime  # noqa: F401  (fail before any job is read)
        except ImportError as exc:
            raise SystemExit(_GRPC_INSTALL) from exc
    configure_logging(args.logfile, "sv")
    log = logging.getLogger("server-func")
    log.info("Started")
    log.info("qid %s server-id %s", args.qid, args.server_id)
    state: dict[str, int] | None = {} if stateful else None
    if args.infile:
        serve_stream(
            args.infile,
            outfiles,
            args.qid,
            args.server_id,
            log,
            state=state,
            grpc_edges=dials,
        )
    else:
        listen = parse_listen(args.ingrpc)
        _serve_grpc(listen, outfiles, dials, args.qid, args.server_id, log, state)
    if state is not None:
        for job_id, visits in sorted(state.items()):
            log.info("state-visits %s %s", job_id, visits)
    log.info("Finished")
    return 0


def _serve_grpc(
    listen,
    outfiles: list[str],
    dials: list[DialSpec],
    qid: str,
    server_id: int,
    logger: logging.Logger,
    state: dict[str, int] | None,
) -> None:
    """Block until Ctrl-C. Each Submit runs one visit on a server thread."""
    try:
        from lib.grpc_runtime import start_server_func_server
    except ImportError as exc:
        raise SystemExit(_GRPC_INSTALL) from exc

    state_lock = threading.Lock() if state is not None else None
    with ExitStack() as stack:
        handles = [
            stack.enter_context(open(path, "a", encoding="utf-8", newline="\n"))
            for path in outfiles
        ]

        def visit(job: Job) -> None:
            serve_job(
                job,
                outfiles,
                qid,
                server_id,
                logger,
                state=state,
                writers=handles,
                grpc_edges=dials,
                state_lock=state_lock,
            )

        server, port = start_server_func_server(listen, visit)
        logger.info("gRPC listen %s:%s workers %s; Ctrl-C to stop", listen.host, port, listen.workers)
        try:
            while True:
                time.sleep(0.2)
        except KeyboardInterrupt:
            logger.info("stopping gRPC listen")
        finally:
            server.stop(1)


def _count_visit(state: dict[str, int], job_id: str, state_lock: threading.Lock | None) -> None:
    if state_lock is None:
        state[job_id] = state.get(job_id, 0) + 1
        return
    with state_lock:
        state[job_id] = state.get(job_id, 0) + 1


def _forward(
    job: Job,
    outfiles: list[str],
    writers: list[TextIO] | None,
    grpc_edges: list[DialSpec],
    qid: str,
    logger: logging.Logger,
    chooser: random.Random,
    submit_fn: SubmitFn | None,
) -> str | None:
    file_writers: list[TextIO | None]
    if writers is None:
        file_writers = [None] * len(outfiles)
    else:
        if len(writers) != len(outfiles):
            raise ValueError("writers must be the same length as outfiles")
        file_writers = list(writers)

    slots: list[tuple[str, int | DialSpec]] = [("file", index) for index in range(len(outfiles))]
    slots.extend(("grpc", edge) for edge in grpc_edges)
    if not slots:
        logger.info("%s", error_no_out_edge(job.job_id, qid))
        return None

    kind, payload = slots[chooser.randrange(len(slots))]
    if kind == "file":
        index = int(payload)
        dest = outfiles[index]
        _write_job(dest, job.raw_line, file_writers[index])
        logger.info("%s", depart_via(job.job_id, qid, dest))
        return dest

    edge = payload if isinstance(payload, DialSpec) else None
    if edge is None:
        raise TypeError("grpc slot was not a DialSpec")
    outcome = _submit(edge, job, qid, logger, submit_fn)
    if outcome == "failed":
        return None
    logger.info("%s", depart_via(job.job_id, qid, edge.address))
    return edge.address


def _submit(
    edge: DialSpec,
    job: Job,
    qid: str,
    logger: logging.Logger,
    submit_fn: SubmitFn | None,
) -> str:
    if submit_fn is not None:
        return submit_fn(edge, job, qid)
    try:
        from lib.grpc_runtime import submit_job
    except ImportError as exc:
        raise SystemExit(_GRPC_INSTALL) from exc
    return submit_job(edge, job, qid, logger=logger)


_GRPC_INSTALL = (
    "gRPC edges need grpcio and protobuf. "
    "pip install -r implementation/obj-a/requirements.txt"
)


def _preview(line: str) -> str:
    shown = line.strip()
    if len(shown) > 200:
        return shown[:200] + "..."
    return shown
