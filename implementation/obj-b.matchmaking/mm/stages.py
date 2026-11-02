"""Stage ids and the routing decisions that differ from a uniform edge pick."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

from lib.grpc_spec import DialSpec
from lib.job import Job

from mm.ticket import BANDS, MODES, REGIONS, Ticket, parse_ticket

STAGES = {
    "validate": 0,
    "rating": 1,
    "route": 2,
    "search": 3,
    "ready": 4,
    "allocate": 5,
}

# q0 admit, q1 rating, q2 route, then the search range.
SEARCH_BASE = 3
# Ready-retry token. It is not a file and not a dial; the bracket table
# replaces it with the search shard for this ticket.
RETRY_SHARD = "retry-shard"


def choose_route(region: str, routes: dict[str, str]) -> str | None:
    """Return the dest registered for ``region``, or None when it has no edge."""
    return routes.get(region)


def search_number(
    region: str,
    mode: str,
    band: str,
    seq: int,
    *,
    replicas: int,
    regions: tuple[str, ...] = REGIONS,
    modes: tuple[str, ...] = MODES,
    bands: tuple[str, ...] = BANDS,
) -> int:
    """Offset of this ticket's search shard within the search range.

    Brackets are region, then mode, then band. The shard is ``seq % replicas``,
    so a later ready-check failure returns to the same queue.
    """
    if replicas < 1:
        raise ValueError("replicas must be >= 1")
    if seq < 0:
        raise ValueError("seq must be >= 0")
    try:
        region_index = regions.index(region)
        mode_index = modes.index(mode)
        band_index = bands.index(band)
    except ValueError as exc:
        raise ValueError(f"ticket token is outside this layout ({exc})") from exc
    bracket = (region_index * len(modes) + mode_index) * len(bands) + band_index
    return bracket * replicas + (seq % replicas)


def search_qid_for_ticket(
    ticket: Ticket,
    *,
    replicas: int,
    search_base: int = SEARCH_BASE,
    regions: tuple[str, ...] = REGIONS,
    modes: tuple[str, ...] = MODES,
    bands: tuple[str, ...] = BANDS,
    single: bool = False,
) -> str:
    """Numeric queue id of the search shard that owns ``ticket``.

    ``single`` is the Small layout: every ticket shares one search queue.
    Skill band still changes the wait. It does not pick a different queue.
    """
    if single:
        return f"q{search_base}"
    offset = search_number(
        ticket.region,
        ticket.mode,
        ticket.band,
        ticket.seq,
        replicas=replicas,
        regions=regions,
        modes=modes,
        bands=bands,
    )
    return f"q{search_base + offset}"


@dataclass(frozen=True)
class BracketTable:
    """Formula parameters plus the open edge for each search qid.

    ``destinations`` maps a search qid to the edge label the chooser returns:
    ``host:port`` or a file path. The process-mode server puts the same label
    on its dial list.
    """

    replicas: int
    search_base: int
    destinations: dict[str, str]
    regions: tuple[str, ...] = REGIONS
    modes: tuple[str, ...] = MODES
    bands: tuple[str, ...] = BANDS
    single: bool = False

    def qid_for(self, ticket: Ticket) -> str:
        return search_qid_for_ticket(
            ticket,
            replicas=self.replicas,
            search_base=self.search_base,
            regions=self.regions,
            modes=self.modes,
            bands=self.bands,
            single=self.single,
        )

    def dest_for(self, ticket: Ticket) -> str | None:
        return self.destinations.get(self.qid_for(ticket))


def load_bracket_table(path: Path) -> BracketTable:
    """Read the JSON written next to a process-mode scenario."""
    data = json.loads(path.read_text(encoding="utf-8"))
    destinations = {
        qid: raw.split(",", 1)[0] if "," in raw else raw
        for qid, raw in data["destinations"].items()
    }
    return BracketTable(
        replicas=int(data["replicas"]),
        search_base=int(data["search_base"]),
        destinations=destinations,
        regions=tuple(data.get("regions", REGIONS)),
        modes=tuple(data.get("modes", MODES)),
        bands=tuple(data.get("bands", BANDS)),
        single=bool(data.get("single", False)),
    )


def choose_ready(
    visits: int,
    rng: random.Random,
    fail_p: float,
    max_visits: int,
    pass_dest: str,
    retry_dest: str,
    fail_dest: str,
) -> str:
    """Pass, send the ticket back to search, or give up after the visit cap.

    ``visits`` is the count after this entry, starting at 1. A pass always
    uses ``pass_dest``. A failure uses ``retry_dest`` while ``visits`` is
    below ``max_visits``, and ``fail_dest`` once the cap is reached.
    """
    if rng.random() < fail_p:
        if visits >= max_visits:
            return fail_dest
        return retry_dest
    return pass_dest


def make_edge_chooser(
    stage: str,
    *,
    routes: dict[str, str] | None = None,
    ready: tuple[str, str, str] | None = None,
    state: dict[str, int] | None = None,
    fail_p: float = 0.0,
    max_visits: int = 1,
    bracket: BracketTable | None = None,
):
    """Build the ``edge_chooser`` for one stage.

    Linear stages return their single open edge. Route reads the region
    token, unless ``bracket`` is set, in which case the ticket's search shard
    is the edge. A single-queue bracket sends every ticket to that one shard.
    Ready rolls ``fail_p`` against the in-process visit count.
    A bracket retry uses that same shard. ``routes`` remains the region map
    used when ``bracket`` is omitted.
    """

    def choose(
        job: Job,
        outfiles: list[str],
        grpc_edges: list[DialSpec],
        rng: random.Random,
    ) -> str | None:
        if stage == "route":
            ticket = parse_ticket(job.job_id)
            if ticket is None:
                return None
            if bracket is not None:
                return bracket.dest_for(ticket)
            if routes is None:
                return None
            return choose_route(ticket.region, routes)
        if stage == "ready":
            if ready is None:
                return None
            visits = 1 if state is None else state.get(job.job_id, 1)
            decision = choose_ready(visits, rng, fail_p, max_visits, ready[0], ready[1], ready[2])
            if bracket is not None and decision == ready[1]:
                ticket = parse_ticket(job.job_id)
                if ticket is None:
                    return None
                return bracket.dest_for(ticket)
            return decision
        if len(outfiles) == 1 and not grpc_edges:
            return outfiles[0]
        if len(grpc_edges) == 1 and not outfiles:
            return grpc_edges[0].address
        return None

    return choose
