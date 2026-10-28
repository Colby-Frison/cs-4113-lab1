"""Stage ids and the routing decisions that differ from a uniform edge pick."""

from __future__ import annotations

import random

from lib.grpc_spec import DialSpec
from lib.job import Job

from mm.ticket import parse_ticket

STAGES = {
    "validate": 0,
    "rating": 1,
    "route": 2,
    "search": 3,
    "ready": 4,
    "allocate": 5,
}


def choose_route(region: str, routes: dict[str, str]) -> str | None:
    """Return the dest registered for ``region``, or None when it has no edge."""
    return routes.get(region)


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
):
    """Build the ``edge_chooser`` for one stage.

    Linear stages return their single open edge. Route reads the region
    token. Ready rolls ``fail_p`` against the in-process visit count.
    """

    def choose(
        job: Job,
        outfiles: list[str],
        grpc_edges: list[DialSpec],
        rng: random.Random,
    ) -> str | None:
        if stage == "route":
            ticket = parse_ticket(job.job_id)
            if ticket is None or routes is None:
                return None
            return choose_route(ticket.region, routes)
        if stage == "ready":
            if ready is None:
                return None
            visits = 1 if state is None else state.get(job.job_id, 1)
            return choose_ready(visits, rng, fail_p, max_visits, ready[0], ready[1], ready[2])
        if len(outfiles) == 1 and not grpc_edges:
            return outfiles[0]
        if len(grpc_edges) == 1 and not outfiles:
            return grpc_edges[0].address
        return None

    return choose
