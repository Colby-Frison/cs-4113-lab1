"""Wait-task visit shared by the stateless and stateful server functions.

One visit sleeps each task in order and logs Lab 0 server events. The original
job line is forwarded unchanged. Optional ``state`` counts visits per job id
inside this process only; it is not written to the next edge.
"""

from __future__ import annotations

import argparse
import logging
import random
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
from lib.job import Job, JobParseError, is_header, parse_job_line
from lib.logsetup import configure_logging, reject_unimplemented_grpc

SleepFn = Callable[[float], None]


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
) -> str | None:
    """Run one visit. Return the outfile path used, or None when there is no edge.

    ``writers``, when passed, must be the same length as ``outfiles`` and is
    used instead of opening ``outfiles`` again. The caller owns those handles.
    """
    chooser = rng or random.Random()
    logger.info("%s", enters_server(job.job_id, qid, server_id))
    if state is not None:
        state[job.job_id] = state.get(job.job_id, 0) + 1

    for task in job.tasks:
        logger.info("%s", task_start(job.job_id, qid, task.tid, task.wait_seconds))
        sleep_fn(task.wait_seconds)
        logger.info("%s", task_end(job.job_id, qid, task.tid, task.wait_seconds))

    logger.info("%s", exits_server(job.job_id, qid, server_id))
    if not outfiles:
        logger.info("%s", error_no_out_edge(job.job_id, qid))
        return None

    index = chooser.randrange(len(outfiles))
    dest = outfiles[index]
    _write_job(dest, job.raw_line, None if writers is None else writers[index])
    logger.info("%s", depart_via(job.job_id, qid, dest))
    return dest


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
) -> None:
    """Read ``infile`` until EOF, sleeping through every wait-task job."""
    chooser = rng or random.Random()
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
    parser.add_argument("-i", "--infile", required=True, help="Job CSV or FIFO to read")
    parser.add_argument(
        "-o",
        "--outfile",
        action="append",
        help="Destination file or FIFO. Repeat for several edges. Omit to log NO-OUT-EDGE.",
    )
    parser.add_argument("--logfile", help="Log path (default: stderr)")
    parser.add_argument("--qid", default="q0", help="Queue id written in events (default: q0)")
    parser.add_argument(
        "--server-id",
        type=int,
        default=0,
        help="Server index written in ENTERS-SERVER / EXITS-SERVER (default: 0)",
    )
    parser.add_argument("--ingrpc", help="Not implemented. interface,num_workers")
    parser.add_argument(
        "--outgrpc",
        action="append",
        help="Not implemented. host:port,num_workers",
    )
    return parser


def run_cli(argv: list[str] | None = None, *, stateful: bool) -> int:
    """Parse arguments, then serve ``--infile`` until EOF."""
    args = build_parser(stateful).parse_args(argv)
    reject_unimplemented_grpc(args.ingrpc, args.outgrpc)
    if args.server_id < 0:
        raise SystemExit("--server-id must be >= 0")
    if not args.qid:
        raise SystemExit("--qid must be non-empty")

    configure_logging(args.logfile, "sv")
    log = logging.getLogger("server-func")
    log.info("Started")
    log.info("qid %s server-id %s", args.qid, args.server_id)
    state: dict[str, int] | None = {} if stateful else None
    serve_stream(
        args.infile,
        list(args.outfile or []),
        args.qid,
        args.server_id,
        log,
        state=state,
    )
    if state is not None:
        for job_id, visits in sorted(state.items()):
            log.info("state-visits %s %s", job_id, visits)
    log.info("Finished")
    return 0


def _preview(line: str) -> str:
    shown = line.strip()
    if len(shown) > 200:
        return shown[:200] + "..."
    return shown
