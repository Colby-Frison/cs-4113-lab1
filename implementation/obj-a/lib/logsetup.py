"""Process logging in the same shape as the course examples.

``configure_logging`` calls ``logging.basicConfig`` once. With no ``--logfile``
the handler is stderr. With a path, the handler is that file. The format string
embeds the pid and hostname so two queues on one machine can be told apart.
Child loggers propagate to this root handler, which is how ``queue.py`` and
``server_func.py`` both land in the file they were given.
"""

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
