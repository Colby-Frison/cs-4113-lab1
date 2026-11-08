"""Parse --ingrpc / --outgrpc without importing grpc.

File and FIFO edges stay the default. A gRPC flag adds one edge beside them.
Parsing lives here so a bad flag fails before grpcio is imported, and so the
file-only tests never load the stubs.

The course comment wrote ``--ingrpc 0.0.0.0,5`` with no port. ``parse_listen``
splits on the comma, then splits the address on the last colon, and rejects
the flag when that colon is missing. ``parse_dial`` does the same and then
picks the proto service from the caller: a queue defaults to queue-to-server,
a trailing ``queue`` selects queue-to-queue, and a server function always
selects server-to-queue.
"""

from __future__ import annotations

from dataclasses import dataclass

# DialSpec.service values. They match the three proto service names.
QUEUE_TO_SERVER = "queue_to_server"
SERVER_TO_QUEUE = "server_to_queue"
QUEUE_TO_QUEUE = "queue_to_queue"


@dataclass(frozen=True)
class ListenSpec:
    """One --ingrpc value: bind address and server thread-pool size."""

    host: str
    port: int
    workers: int

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass(frozen=True)
class DialSpec:
    """One --outgrpc value: peer address, sender threads, and which proto to call."""

    host: str
    port: int
    workers: int
    service: str

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"


def parse_listen(text: str) -> ListenSpec:
    """Parse ``interface:port,workers`` (example ``0.0.0.0:50051,5``)."""
    address, workers = _split_address_and_workers(text, "ingrpc")
    host, port = _split_host_port(address, "ingrpc")
    return ListenSpec(host=host, port=port, workers=workers)


def parse_dial(text: str, *, caller: str) -> DialSpec:
    """Parse ``host:port,workers`` with an optional role.

    ``caller`` is ``queue`` or ``server`` (the process that owns the flag).

    A queue defaults to the queue-to-server service. Pass a trailing ``queue``
    to call another queue (queue-to-queue). A server function always calls
    server-to-queue; the role may be omitted or set to ``queue``.
    """
    if caller not in ("queue", "server"):
        raise ValueError("caller must be queue or server")
    parts = text.split(",")
    if len(parts) == 2:
        address, workers_text = parts
        role = None
    elif len(parts) == 3:
        address, workers_text, role = parts
        role = role.strip()
    else:
        raise SystemExit(
            "--outgrpc must be host:port,workers or host:port,workers,role "
            f"(got {text!r})"
        )
    host, port = _split_host_port(address, "outgrpc")
    workers = _parse_workers(workers_text, "outgrpc")
    service = _service_for_caller(caller, role, text)
    return DialSpec(host=host, port=port, workers=workers, service=service)


def _service_for_caller(caller: str, role: str | None, text: str) -> str:
    if caller == "server":
        if role not in (None, "queue"):
            raise SystemExit(
                "server function --outgrpc calls a queue (server-to-queue). "
                f"Omit the role or pass queue (got {text!r})."
            )
        return SERVER_TO_QUEUE
    if role in (None, "server"):
        return QUEUE_TO_SERVER
    if role == "queue":
        return QUEUE_TO_QUEUE
    raise SystemExit(
        "queue --outgrpc role must be server (a server function) or queue "
        f"(another queue). Got {text!r}."
    )


def _split_address_and_workers(text: str, flag: str) -> tuple[str, int]:
    parts = text.split(",")
    if len(parts) != 2:
        raise SystemExit(
            f"--{flag} must be interface:port,workers "
            f"(example 0.0.0.0:50051,5). Got {text!r}."
        )
    return parts[0].strip(), _parse_workers(parts[1], flag)


def _split_host_port(address: str, flag: str) -> tuple[str, int]:
    address = address.strip()
    if ":" not in address:
        raise SystemExit(
            f"--{flag} address {address!r} needs a port. "
            "The course example omitted it; use interface:port,workers "
            "(example 0.0.0.0:50051,5)."
        )
    host, port_text = address.rsplit(":", 1)
    if host == "" or port_text == "":
        raise SystemExit(f"--{flag} address {address!r} is not host:port")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise SystemExit(f"--{flag} port {port_text!r} is not an integer") from exc
    if not 0 <= port <= 65535:
        raise SystemExit(f"--{flag} port must be in 0..65535")
    return host, port


def _parse_workers(text: str, flag: str) -> int:
    try:
        workers = int(text.strip())
    except ValueError as exc:
        raise SystemExit(f"--{flag} workers {text!r} is not an integer") from exc
    if workers < 1:
        raise SystemExit(f"--{flag} workers must be >= 1")
    return workers
