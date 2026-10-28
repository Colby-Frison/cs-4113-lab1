#!/usr/bin/env python3
"""Container healthcheck. The environment selects which fact to test.

HEALTH_TCP is a port on this container. HEALTH_READY is a file another
container publishes. HEALTH_EXIST is a comma-separated list of paths.
HEALTH_NEEDLE must appear in PID 1's command line. Any failed check fails
the probe. With none of them set, the probe fails closed.
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path


def main() -> int:
    tcp = os.environ.get("HEALTH_TCP", "")
    ready = os.environ.get("HEALTH_READY", "")
    exist = os.environ.get("HEALTH_EXIST", "")
    needle = os.environ.get("HEALTH_NEEDLE", "")
    if not any((tcp, ready, exist, needle)):
        return 1
    try:
        if tcp:
            with socket.create_connection(("127.0.0.1", int(tcp)), 2):
                pass
        if ready and not os.path.exists(ready):
            return 1
        for path in filter(None, exist.split(",")):
            if not os.path.exists(path):
                return 1
        if needle:
            command = Path("/proc/1/cmdline").read_bytes().replace(b"\0", b" ")
            if needle.encode() not in command:
                return 1
    except OSError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
