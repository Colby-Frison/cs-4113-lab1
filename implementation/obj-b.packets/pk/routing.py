"""Addresses, routing tables, and the queue address book.

Router ``i`` owns ``10.{i >> 8}.{i & 255}.0/24``. A routing table is a text file
with one ``prefix<TAB>next hop`` line per route, where the next hop is a queue id
or ``local``. Lookup is longest-prefix match. ``addresses.csv`` says where each
queue id is running, so a table never names a host.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

LOCAL = "local"
ADDRESS_FIELDS = ("qid", "host", "queue_port", "server_port")
QUEUE_BASE_PORT = 20000
SERVER_BASE_PORT = 26000
# Both port ranges stay below the usual ephemeral range, which starts at 32768.
MAX_QUEUES_PER_HOST = SERVER_BASE_PORT - QUEUE_BASE_PORT
MAX_ROUTERS = 65536


def router_prefix(router: int) -> str:
    """The ``/24`` owned by a router."""
    if not 0 <= router < MAX_ROUTERS:
        raise ValueError(f"router index must be in 0..{MAX_ROUTERS - 1}")
    return f"10.{router >> 8}.{router & 255}.0/24"


def router_network(router: int) -> int:
    """The first address of a router's prefix, as an integer."""
    return (10 << 24) | (router << 8)


def _parse_prefix(text: str) -> tuple[int, int]:
    """Return ``(network, length)`` for ``a.b.c.d/n``."""
    address, slash, length_text = text.partition("/")
    octets = address.split(".")
    if not slash or len(octets) != 4:
        raise ValueError(f"bad prefix {text!r}")
    value = 0
    for octet in octets:
        number = int(octet)
        if not 0 <= number <= 255:
            raise ValueError(f"bad prefix {text!r}")
        value = (value << 8) | number
    length = int(length_text)
    if not 0 <= length <= 32:
        raise ValueError(f"bad prefix {text!r}")
    return value & _mask(length), length


def _mask(length: int) -> int:
    return (0xFFFFFFFF << (32 - length)) & 0xFFFFFFFF


class RoutingTable:
    """Longest-prefix match over one dict per prefix length."""

    def __init__(self, routes: list[tuple[str, str]]) -> None:
        by_length: dict[int, dict[int, str]] = {}
        for prefix, next_hop in routes:
            network, length = _parse_prefix(prefix)
            by_length.setdefault(length, {})[network] = next_hop
        self._levels = [
            (_mask(length), by_length[length]) for length in sorted(by_length, reverse=True)
        ]

    @classmethod
    def load(cls, path: str | Path) -> "RoutingTable":
        routes: list[tuple[str, str]] = []
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                text = line.strip()
                if not text or text.startswith("#"):
                    continue
                fields = text.split("\t")
                if len(fields) != 2:
                    raise ValueError(f"{path}:{number}: expected prefix<TAB>next hop")
                routes.append((fields[0], fields[1]))
        return cls(routes)

    def lookup(self, dst_ip: int) -> str | None:
        """Next hop for an address, or None when no route matches."""
        for mask, routes in self._levels:
            next_hop = routes.get(dst_ip & mask)
            if next_hop is not None:
                return next_hop
        return None


@dataclass(frozen=True)
class QueueAddress:
    qid: str
    host: str
    queue_port: int
    server_port: int


def load_addresses(path: str | Path) -> dict[str, QueueAddress]:
    with open(path, encoding="utf-8", newline="") as handle:
        return {
            row["qid"]: QueueAddress(
                row["qid"], row["host"], int(row["queue_port"]), int(row["server_port"])
            )
            for row in csv.DictReader(handle)
        }


def write_addresses(qids: list[str], hosts: list[str], path: str | Path) -> None:
    """Spread queue ids evenly over hosts. All of a router's queues stay together.

    Ports are numbered from each host's first queue, so every host has room for
    ``MAX_QUEUES_PER_HOST`` queues.
    """
    if not hosts:
        raise ValueError("at least one host is required")
    rows = []
    taken: dict[str, int] = {}
    for index, qid in enumerate(qids):
        host = hosts[index * len(hosts) // len(qids)]
        number = taken.get(host, 0)
        if number >= MAX_QUEUES_PER_HOST:
            raise ValueError(f"{host} would need more than {MAX_QUEUES_PER_HOST} queues")
        taken[host] = number + 1
        rows.append([qid, host, QUEUE_BASE_PORT + number, SERVER_BASE_PORT + number])
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(ADDRESS_FIELDS)
        writer.writerows(rows)


def loopback_hosts(queues: int) -> list[str]:
    """Enough loopback addresses for ``queues`` queues on one machine."""
    count = max(1, -(-queues // MAX_QUEUES_PER_HOST))
    return [f"127.0.0.{index + 1}" for index in range(count)]
