#!/usr/bin/env python3
"""Start one matchmaking container.

The image entrypoint waits until the destinations this process dials are
accepting connections, then replaces itself with the queue, stage server, or
workload generator. HOLD_FIFOS keeps every named pipe open from both ends so
the other containers can open them in any order. SIGNAL_FIFO starts the real
program as a child and publishes READY_FILE once that child has the pipe open,
which is how the generator learns the admit queue is reading.
"""

from __future__ import annotations

import os
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    timeout = float(os.environ.get("WAIT_TIMEOUT", "180"))
    if os.environ.get("HOLD_FIFOS"):
        _hold(
            _split(os.environ["HOLD_FIFOS"]),
            _split(os.environ.get("CLEAR_FILES", "")),
        )
        return 0
    _wait_paths(_split(os.environ.get("WAIT_PATH", "")), timeout)
    _wait_ports(_split(os.environ.get("WAIT_FOR", "")), timeout)
    if not args:
        raise SystemExit("entrypoint needs the program to run")
    if os.environ.get("SIGNAL_FIFO"):
        return _launch(args, timeout)
    os.execvp(args[0], args)
    return 0


def _hold(paths: list[str], clear: list[str]) -> None:
    """Create any missing FIFOs, hold them open, and sleep until stopped."""
    descriptors: list[int] = []
    created = False
    for path in paths:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        if not _is_fifo(path):
            if os.path.lexists(path):
                os.remove(path)
            os.mkfifo(path, 0o666)
            created = True
        descriptors.append(os.open(path, os.O_RDWR))
    if created:
        for path in clear:
            if os.path.lexists(path):
                os.remove(path)
    if not descriptors:
        raise SystemExit("HOLD_FIFOS did not name a FIFO")

    def _stop(signum: int, _frame: object) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while True:
        time.sleep(3600)


def _launch(args: list[str], timeout: float) -> int:
    """Run args and publish READY_FILE once the child has SIGNAL_FIFO open."""
    fifo = os.environ["SIGNAL_FIFO"]
    ready = Path(os.environ["READY_FILE"])
    process = subprocess.Popen(args)

    def _forward(signum: int, _frame: object) -> None:
        if process.poll() is None:
            process.send_signal(signum)

    signal.signal(signal.SIGTERM, _forward)
    signal.signal(signal.SIGINT, _forward)
    deadline = time.monotonic() + timeout
    published = False
    while process.poll() is None:
        if not published and _process_has_path(process.pid, fifo):
            ready.parent.mkdir(parents=True, exist_ok=True)
            ready.write_text("ready\n", encoding="utf-8")
            published = True
        if not published and time.monotonic() >= deadline:
            process.terminate()
            process.wait(timeout=5)
            raise SystemExit(f"timed out waiting for {fifo} to be opened")
        time.sleep(0.2)
    return int(process.wait())


def _wait_ports(specs: list[str], timeout: float) -> None:
    for spec in specs:
        host, separator, port_text = spec.rpartition(":")
        if not host or not separator:
            raise SystemExit(f"WAIT_FOR entry must be host:port (got {spec!r})")
        _wait(lambda h=host, p=int(port_text): _port_open(h, p), timeout, spec)


def _wait_paths(paths: list[str], timeout: float) -> None:
    for path in paths:
        _wait(lambda item=path: os.path.exists(item), timeout, path)


def _wait(ready, timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while not ready():
        if time.monotonic() >= deadline:
            raise SystemExit(f"timed out waiting for {label}")
        time.sleep(0.3)


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), 2):
            return True
    except OSError:
        return False


def _process_has_path(pid: int, path: str) -> bool:
    fd_dir = Path(f"/proc/{pid}/fd")
    if not fd_dir.is_dir():
        return False
    for entry in fd_dir.iterdir():
        try:
            if os.readlink(entry) == path:
                return True
        except OSError:
            continue
    return False


def _is_fifo(path: str) -> bool:
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return False
    return stat.S_ISFIFO(mode)


def _split(text: str) -> list[str]:
    return [item for item in text.split(",") if item]


if __name__ == "__main__":
    raise SystemExit(main())
