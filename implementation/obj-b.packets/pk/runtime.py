"""Command line and gRPC listen loop for the router server functions."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import time

from lib.grpc_spec import parse_listen
from lib.logsetup import configure_logging

from pk.router import STAGES, Router, Sink
from pk.routing import RoutingTable, load_addresses

MONOLITH = "monolith"
NEEDS_ROUTES = (MONOLITH, "lookup")
NEEDS_SINK = (MONOLITH, "forward")
NEEDS_NEXT_QID = ("validate", "lookup")


def build_parser(description: str, *, with_stage: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="server-func", description=description)
    if with_stage:
        parser.add_argument("--stage", required=True, choices=sorted(STAGES))
        parser.add_argument("--next-qid", help="Queue of the next stage (validate, lookup)")
    parser.add_argument("--qid", required=True, help="Queue id written in events")
    parser.add_argument("--server-id", type=int, default=0)
    parser.add_argument(
        "--ingrpc", required=True, help="Listen address and server count: interface:port,workers"
    )
    parser.add_argument("--addresses", required=True, help="addresses.csv")
    parser.add_argument("--routes", help="This router's routing table")
    parser.add_argument("--sink", help="File for packets delivered here")
    parser.add_argument("--logfile", help="Log path (default: stderr)")
    return parser


def run_cli(stage: str | None, argv: list[str] | None, description: str) -> int:
    parser = build_parser(description, with_stage=stage is None)
    args = parser.parse_args(argv)
    stage = stage or args.stage
    if stage in NEEDS_ROUTES and not args.routes:
        parser.error(f"{stage} needs --routes")
    if stage in NEEDS_SINK and not args.sink:
        parser.error(f"{stage} needs --sink")
    if stage in NEEDS_NEXT_QID and not args.next_qid:
        parser.error(f"{stage} needs --next-qid")
    listen = parse_listen(args.ingrpc)

    from lib.grpc_runtime import start_server_func_server

    configure_logging(args.logfile, "sv")
    log = logging.getLogger("server-func")
    log.info("Started")
    log.info("qid %s server-id %s stage %s", args.qid, args.server_id, stage)
    table = RoutingTable.load(args.routes) if stage in NEEDS_ROUTES else None
    handle = open(args.sink, "a", encoding="utf-8", newline="\n") if stage in NEEDS_SINK else None
    try:
        router = Router(
            args.qid,
            args.server_id,
            log,
            addresses=load_addresses(args.addresses),
            table=table,
            sink=Sink(handle, os.path.basename(args.sink)) if handle else None,
            next_qid=getattr(args, "next_qid", None),
        )
        visit = getattr(router, stage)
        # A shell's background job starts with SIGINT ignored, so set it here.
        signal.signal(signal.SIGINT, signal.default_int_handler)
        signal.signal(signal.SIGTERM, signal.default_int_handler)
        server, port = start_server_func_server(listen, visit)
        log.info("gRPC listen %s:%s workers %s; Ctrl-C to stop", listen.host, port, listen.workers)
        try:
            while True:
                time.sleep(0.2)
        except KeyboardInterrupt:
            log.info("stopping gRPC listen")
        finally:
            server.stop(1).wait()
    finally:
        if handle:
            handle.close()
    log.info("Finished")
    return 0
