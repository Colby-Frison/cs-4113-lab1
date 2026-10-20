"""Process logging in the same shape as the course examples."""

from __future__ import annotations

import logging
import os
import socket


def configure_logging(logfile: str | None, component: str) -> None:
    """Send INFO logs to stderr, or to ``logfile`` when one is given.

    ``component`` is ``Q`` for the queue and ``sv`` for a server function, so
    the prefix matches the course examples (``Q_pid…``, ``sv_pid…``).
    """
    log_format = (
        f"%(asctime)s - {component}_pid{os.getpid()}@{socket.gethostname()} "
        "- %(levelname)s - %(message)s"
    )
    if logfile is None:
        logging.basicConfig(level=logging.INFO, format=log_format)
    else:
        logging.basicConfig(filename=logfile, level=logging.INFO, format=log_format)


def reject_unimplemented_grpc(in_grpc: object, out_grpc: object) -> None:
    """gRPC edges are a later task. Fail instead of ignoring the flags."""
    if in_grpc or out_grpc:
        raise SystemExit(
            "gRPC inputs and outputs are not implemented. Use --infile and --outfile."
        )
