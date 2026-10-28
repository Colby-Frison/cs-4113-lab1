"""Run one matchmaking server process, monolith or a single stage.

The file path sleeps through ``--infile`` and exits at EOF. ``--ingrpc``
binds ``QueueToServer`` and stays up until Ctrl-C. grpc is imported only
after one of those RPC flags is present.
"""

from __future__ import annotations

import argparse
import logging
import random
import time
from contextlib import ExitStack
from pathlib import Path

from lib.grpc_spec import DialSpec, parse_dial, parse_listen
from lib.job import Job, JobParseError, is_header, parse_job_line
from lib.logsetup import configure_logging
from lib.server import build_parser, serve_job, serve_until_interrupt

from mm.stages import STAGES, make_edge_chooser
from mm.statefile import load_state, save_state
from mm.ticket import parse_ticket

_GRPC_INSTALL = (
    "gRPC edges need grpcio and protobuf. "
    "pip install -r implementation/obj-a/requirements.txt"
)


def run_monolith(argv: list[str] | None = None) -> int:
    """Sleep tids 0 through 5 in one visit and forward the original row."""
    parser = build_parser(False)
    parser.description = (
        "Monolithic matchmaking server. One visit sleeps tasks 0 through 5 "
        "and forwards the original ticket."
    )
    args = parser.parse_args(argv)
    _require_io(args)
    return _dispatch(
        args,
        label="monolith",
        only_tids=None,
        routes=None,
        ready=None,
        fail_p=0.0,
        max_visits=1,
        state_path=None,
        seed=None,
        accept_mode="complete",
    )


def run_stage(argv: list[str] | None = None) -> int:
    """Sleep one stage's task and apply that stage's routing rule."""
    parser = argparse.ArgumentParser(
        description="One matchmaking stage. Sleeps that stage's task and forwards the original ticket."
    )
    parser.add_argument("--stage", required=True, choices=tuple(STAGES))
    parser.add_argument("-i", "--infile", help="Job CSV or FIFO. Omit when --ingrpc is set.")
    parser.add_argument("-o", "--outfile", action="append", help="Downstream file or FIFO.")
    parser.add_argument("--outgrpc", action="append", help="Downstream queue: host:port,workers.")
    parser.add_argument("--ingrpc", help="Listen: interface:port,workers.")
    parser.add_argument("--logfile", help="Log path (default: stderr).")
    parser.add_argument("--qid", default="q0")
    parser.add_argument("--server-id", type=int, default=0)
    parser.add_argument("--seed", type=int, default=None, help="RNG seed for the ready-check roll.")
    parser.add_argument(
        "--route-edge",
        action="append",
        default=[],
        help="region=path or region=host:port,workers. Repeat per region.",
    )
    parser.add_argument("--pass", dest="pass_dest", help="Ready-check pass destination.")
    parser.add_argument("--retry", dest="retry_dest", help="Ready-check failure destination.")
    parser.add_argument("--fail", dest="fail_dest", help="Destination once the visit cap is reached.")
    parser.add_argument("--ready-fail-p", type=float, default=0.2)
    parser.add_argument("--max-ready-visits", type=int, default=2)
    parser.add_argument("--state-file", help="Persist the ready-check visit count here.")
    args = parser.parse_args(argv)
    _require_io(args)
    routes, ready, outfiles, dials = _stage_edges(args)
    return _dispatch(
        args,
        label=args.stage,
        only_tids={STAGES[args.stage]},
        routes=routes,
        ready=ready,
        fail_p=args.ready_fail_p,
        max_visits=args.max_ready_visits,
        state_path=Path(args.state_file) if args.state_file else None,
        seed=args.seed,
        accept_mode="stage",
        outfiles=outfiles,
        dials=dials,
    )


def _stage_edges(
    args: argparse.Namespace,
) -> tuple[dict[str, str] | None, tuple[str, str, str] | None, list[str], list[DialSpec]]:
    """Split this stage's destinations into files and dials."""
    stage = args.stage
    if stage == "route":
        routes: dict[str, str] = {}
        outfiles: list[str] = []
        dials: list[DialSpec] = []
        if not args.route_edge:
            raise SystemExit("--stage route requires at least one --route-edge")
        for item in args.route_edge:
            region, dest = _split_route(item)
            kind, stored = _classify_dest(dest)
            routes[region] = stored
            if kind == "file":
                outfiles.append(stored)
            else:
                dials.append(parse_dial(dest, caller="server"))
        return routes, None, outfiles, dials

    if stage == "ready":
        _check_ready_args(args)
        named = (args.pass_dest, args.retry_dest, args.fail_dest)
        if len(set(named)) != 3:
            raise SystemExit("--pass, --retry, and --fail must be three different destinations")
        outfiles = []
        dials = []
        resolved: list[str] = []
        for dest in named:
            kind, stored = _classify_dest(dest)
            resolved.append(stored)
            if kind == "file":
                outfiles.append(stored)
            else:
                dials.append(parse_dial(dest, caller="server"))
        return None, (resolved[0], resolved[1], resolved[2]), outfiles, dials

    outfiles = list(args.outfile or [])
    dials = [parse_dial(item, caller="server") for item in (args.outgrpc or [])]
    if len(outfiles) + len(dials) != 1:
        raise SystemExit(f"--stage {stage} requires exactly one --outfile or --outgrpc")
    if args.route_edge or args.pass_dest or args.retry_dest or args.fail_dest:
        raise SystemExit(f"--stage {stage} does not take route or ready edges")
    return None, None, outfiles, dials


def _split_route(item: str) -> tuple[str, str]:
    if "=" not in item:
        raise SystemExit(f"--route-edge must be region=dest (got {item!r})")
    region, dest = item.split("=", 1)
    if not region or not dest:
        raise SystemExit(f"--route-edge must be region=dest (got {item!r})")
    return region, dest


def _classify_dest(dest: str) -> tuple[str, str]:
    """A comma marks ``host:port,workers``. Anything else is a file path.

    The stored label for a dial is ``host:port``, which is what DEPART-VIA prints.
    """
    if "," not in dest:
        return "file", dest
    dial = parse_dial(dest, caller="server")
    return "grpc", dial.address


def _check_ready_args(args: argparse.Namespace) -> None:
    if args.ready_fail_p < 0 or args.ready_fail_p > 1:
        raise SystemExit("--ready-fail-p must be between 0 and 1")
    if args.max_ready_visits < 1:
        raise SystemExit("--max-ready-visits must be >= 1")
    if not args.pass_dest or not args.retry_dest or not args.fail_dest:
        raise SystemExit("--stage ready requires --pass, --retry, and --fail")


def _require_io(args: argparse.Namespace) -> None:
    if args.server_id < 0:
        raise SystemExit("--server-id must be >= 0")
    if not args.qid:
        raise SystemExit("--qid must be non-empty")
    if args.infile and args.ingrpc:
        raise SystemExit("pass either --infile or --ingrpc, not both")
    if not args.infile and not args.ingrpc:
        raise SystemExit("need --infile or --ingrpc")


def _dispatch(
    args: argparse.Namespace,
    *,
    label: str,
    only_tids: set[int] | None,
    routes: dict[str, str] | None,
    ready: tuple[str, str, str] | None,
    fail_p: float,
    max_visits: int,
    state_path: Path | None,
    seed: int | None,
    accept_mode: str,
    outfiles: list[str] | None = None,
    dials: list[DialSpec] | None = None,
) -> int:
    if outfiles is None:
        outfiles = list(args.outfile or [])
    if dials is None:
        dials = [parse_dial(item, caller="server") for item in (args.outgrpc or [])]
    if dials or args.ingrpc:
        try:
            import lib.grpc_runtime  # noqa: F401
        except ImportError as exc:
            raise SystemExit(_GRPC_INSTALL) from exc

    _configure_logging(args.logfile)
    log = logging.getLogger("match-server")
    log.handlers.clear()
    log.propagate = True
    log.info("Started")
    log.info("stage %s qid %s server-id %s", label, args.qid, args.server_id)

    state = load_state(state_path) if state_path is not None else None
    if accept_mode == "ready" or (label == "ready"):
        if state is None:
            state = {}
    rng = random.Random(seed)
    chooser = None
    if label != "monolith":
        chooser = make_edge_chooser(
            label,
            routes=routes,
            ready=ready,
            state=state,
            fail_p=fail_p,
            max_visits=max_visits,
        )

    def accept(job: Job) -> bool:
        return _accept(job, log, only_tids)

    if args.infile:
        _serve_file(
            args.infile,
            outfiles,
            dials,
            args.qid,
            args.server_id,
            log,
            only_tids=only_tids,
            edge_chooser=chooser,
            state=state,
            rng=rng,
            accept=accept,
        )
    else:
        listen = parse_listen(args.ingrpc)
        serve_until_interrupt(
            listen,
            outfiles,
            dials,
            args.qid,
            args.server_id,
            log,
            state,
            only_tids=only_tids,
            edge_chooser=chooser,
            rng=rng,
            admit=accept,
        )
    try:
        if state is not None:
            for job_id, visits in sorted(state.items()):
                log.info("state-visits %s %s", job_id, visits)
            if state_path is not None:
                save_state(state_path, state)
        log.info("Finished")
        return 0
    finally:
        _close_logging()


def _close_logging() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()


def _configure_logging(logfile: str | None) -> None:
    """Install the course prefix. A second call in this process replaces the first."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    configure_logging(logfile, "sv")


def _accept(job: Job, logger: logging.Logger, only_tids: set[int] | None) -> bool:
    if parse_ticket(job.job_id) is None:
        logger.error("skipping %s: job id is not a party ticket", job.job_id)
        return False
    present = {task.tid for task in job.tasks}
    needed = range(6) if only_tids is None else only_tids
    missing = [tid for tid in needed if tid not in present]
    if missing:
        logger.error("skipping %s: missing task ids %s", job.job_id, missing)
        return False
    return True


def _serve_file(
    infile: str,
    outfiles: list[str],
    dials: list[DialSpec],
    qid: str,
    server_id: int,
    logger: logging.Logger,
    *,
    only_tids: set[int] | None,
    edge_chooser,
    state: dict[str, int] | None,
    rng: random.Random,
    accept,
) -> None:
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
            if not accept(job):
                continue
            serve_job(
                job,
                outfiles,
                qid,
                server_id,
                logger,
                state=state,
                sleep_fn=time.sleep,
                rng=rng,
                writers=handles,
                grpc_edges=dials,
                only_tids=only_tids,
                edge_chooser=edge_chooser,
            )


def _preview(line: str) -> str:
    shown = line.strip()
    if len(shown) > 200:
        return shown[:200] + "..."
    return shown
