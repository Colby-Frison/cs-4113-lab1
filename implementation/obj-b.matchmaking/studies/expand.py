"""Stamp a matchmaking size pattern into numeric queues.

A person edits ``pattern.json``. This module copies the search stage across
region, mode, and skill band, then shards each bracket ``replicas`` ways.
Queue ids stay numeric so compute-use can read them. ``baseline.json`` is the
product topology and is not an input here.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

MM_ROOT = Path(__file__).resolve().parents[1]
OBJ_A = MM_ROOT.parent / "obj-a"
for _entry in (str(MM_ROOT), str(OBJ_A)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from mm.stages import SEARCH_BASE, search_qid_for_ticket  # noqa: E402
from mm.ticket import BANDS, MODES, REGIONS, Ticket  # noqa: E402

# One process pair per queue is reliable through the medium band. Above this,
# the runner uses the hosted pair instead.
PROCESS_QUEUE_LIMIT = 80

SHARED_FRONT = (
    ("admit", "validate"),
    ("rating", "rating"),
    ("route", "route"),
)
SHARED_BACK = (
    ("ready", "ready"),
    ("allocate", "allocate"),
)
QCONF_HEADER = (
    "Sys ID,Queue Max Size,Num. Servers,Server Function,"
    "Server Function Rate,Source,Sink,Edges in,Edges out"
)
AXES = ("region", "mode", "band")
SHARED_STAGES = ("admit", "rating", "route", "ready", "allocate")


@dataclass(frozen=True)
class QueueSpec:
    """One logical queue and the stage server that drains it."""

    qid: str
    number: int
    stage: str
    server: str
    qsize: int
    workers: int
    bracket: str | None = None
    replica: int | None = None
    next_qid: str | None = None


@dataclass(frozen=True)
class Layout:
    """Expanded queues for one size, before a server-factor sweep."""

    size: str
    replicas: int
    regions: tuple[str, ...]
    modes: tuple[str, ...]
    bands: tuple[str, ...]
    search_base: int
    queues: tuple[QueueSpec, ...]
    admit_qid: str
    ready_qid: str
    allocate_qid: str
    qsize: int
    count: int
    time_scale: float
    ready_fail_p: float
    max_ready_visits: int
    target_rho: float
    seed: int
    baseline_search_workers: int
    single_search: bool
    swept_stages: tuple[str, ...]

    @property
    def search_queues(self) -> tuple[QueueSpec, ...]:
        return tuple(queue for queue in self.queues if queue.stage == "search")

    def search_qid(self, ticket: Ticket) -> str:
        """Search shard for this ticket. Route and ready-retry both use it."""
        return search_qid_for_ticket(
            ticket,
            replicas=self.replicas,
            search_base=self.search_base,
            regions=self.regions,
            modes=self.modes,
            bands=self.bands,
            single=self.single_search,
        )


def load_pattern(path: Path) -> dict:
    """Read one size pattern. The axes and stages are checked, not inferred."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if tuple(data.get("axes", ())) != AXES:
        raise ValueError(f"{path} axes must be {AXES}")
    if tuple(data.get("shared_stages", ())) != SHARED_STAGES:
        raise ValueError(f"{path} shared_stages must be {SHARED_STAGES}")
    if data.get("replicated_stage") != "search":
        raise ValueError(f"{path} replicated_stage must be search")
    replicas = int(data["replicas"])
    if replicas < 1:
        raise ValueError("replicas must be >= 1")
    if int(data["baseline_search_workers"]) < 1:
        raise ValueError("baseline_search_workers must be >= 1")
    if int(data["shared_workers"]) < 1:
        raise ValueError("shared_workers must be >= 1")
    if int(data["count"]) < 1 or int(data["qsize"]) < 1:
        raise ValueError("count and qsize must be >= 1")
    grouping = data.get("search_grouping", "bracket")
    if grouping not in ("bracket", "single"):
        raise ValueError(f"{path} search_grouping must be bracket or single")
    if grouping == "single" and replicas != 1:
        raise ValueError(f"{path} a single search queue uses replicas 1")
    swept = tuple(data.get("swept_stages", ("search",)))
    if "search" not in swept:
        raise ValueError(f"{path} swept_stages must include search")
    known = {"admit", "rating", "route", "search", "ready", "allocate"}
    unknown = [stage for stage in swept if stage not in known]
    if unknown:
        raise ValueError(f"{path} unknown swept stage {unknown[0]}")
    return data


def build_layout(
    pattern: dict,
    *,
    regions: tuple[str, ...] | None = None,
    modes: tuple[str, ...] | None = None,
    bands: tuple[str, ...] | None = None,
) -> Layout:
    """Expand ``pattern`` into one spec per queue.

    The optional axes shrink a layout for a hosted smoke test. A size pattern
    leaves them unset and uses the full ticket tables.
    """
    region_axis = regions if regions is not None else tuple(pattern.get("regions", REGIONS))
    mode_axis = modes if modes is not None else tuple(pattern.get("modes", MODES))
    band_axis = bands if bands is not None else tuple(pattern.get("bands", BANDS))
    replicas = int(pattern["replicas"])
    grouping = pattern.get("search_grouping", "bracket")
    if grouping not in ("bracket", "single"):
        raise ValueError("search_grouping must be bracket or single")
    single = grouping == "single"
    if single and replicas != 1:
        raise ValueError("a single search queue uses replicas 1")
    swept = tuple(pattern.get("swept_stages", ("search",)))
    for token in region_axis:
        if token not in REGIONS:
            raise ValueError(f"unknown region {token}")
    for token in mode_axis:
        if token not in MODES:
            raise ValueError(f"unknown mode {token}")
    for token in band_axis:
        if token not in BANDS:
            raise ValueError(f"unknown band {token}")

    qsize = int(pattern["qsize"])
    shared_workers = int(pattern["shared_workers"])
    search_workers = int(pattern["baseline_search_workers"])

    def workers_for(stage: str) -> int:
        if stage in swept:
            return search_workers
        return shared_workers

    search_count = 1 if single else len(region_axis) * len(mode_axis) * len(band_axis) * replicas
    ready_number = SEARCH_BASE + search_count
    allocate_number = ready_number + 1

    queues: list[QueueSpec] = []
    number = 0
    for stage, server in SHARED_FRONT:
        queues.append(
            QueueSpec(
                qid=f"q{number}",
                number=number,
                stage=stage,
                server=server,
                qsize=qsize,
                workers=workers_for(stage),
            )
        )
        number += 1
    if number != SEARCH_BASE:
        raise RuntimeError(f"search base drifted to {number}")

    if single:
        queues.append(
            QueueSpec(
                qid=f"q{number}",
                number=number,
                stage="search",
                server="search",
                qsize=qsize,
                workers=workers_for("search"),
                next_qid=f"q{ready_number}",
            )
        )
        number += 1
    else:
        for region in region_axis:
            for mode in mode_axis:
                for band in band_axis:
                    for replica in range(replicas):
                        queues.append(
                            QueueSpec(
                                qid=f"q{number}",
                                number=number,
                                stage="search",
                                server="search",
                                qsize=qsize,
                                workers=workers_for("search"),
                                bracket=f"{region}.{mode}.{band}",
                                replica=replica,
                                next_qid=f"q{ready_number}",
                            )
                        )
                        number += 1

    for stage, server in SHARED_BACK:
        queues.append(
            QueueSpec(
                qid=f"q{number}",
                number=number,
                stage=stage,
                server=server,
                qsize=qsize,
                workers=workers_for(stage),
            )
        )
        number += 1

    admit, rating, route = queues[0], queues[1], queues[2]
    queues[0] = QueueSpec(
        qid=admit.qid,
        number=admit.number,
        stage=admit.stage,
        server=admit.server,
        qsize=admit.qsize,
        workers=admit.workers,
        next_qid=rating.qid,
    )
    queues[1] = QueueSpec(
        qid=rating.qid,
        number=rating.number,
        stage=rating.stage,
        server=rating.server,
        qsize=rating.qsize,
        workers=rating.workers,
        next_qid=route.qid,
    )
    queues[2] = QueueSpec(
        qid=route.qid,
        number=route.number,
        stage=route.stage,
        server=route.server,
        qsize=route.qsize,
        workers=route.workers,
        next_qid=None,
    )

    return Layout(
        size=str(pattern.get("size", "")),
        replicas=replicas,
        regions=tuple(region_axis),
        modes=tuple(mode_axis),
        bands=tuple(band_axis),
        search_base=SEARCH_BASE,
        queues=tuple(queues),
        admit_qid="q0",
        ready_qid=f"q{ready_number}",
        allocate_qid=f"q{allocate_number}",
        qsize=qsize,
        count=int(pattern["count"]),
        time_scale=float(pattern["time_scale"]),
        ready_fail_p=float(pattern["ready_fail_p"]),
        max_ready_visits=int(pattern["max_ready_visits"]),
        target_rho=float(pattern["target_rho"]),
        seed=int(pattern["seed"]),
        baseline_search_workers=search_workers,
        single_search=single,
        swept_stages=swept,
    )


def write_servers_csv(layout: Layout, path: Path) -> None:
    """``qid,qsize,servers`` rows. analyze_run reads this as ``--qconf-file``."""
    lines = ["qid,qsize,servers"]
    for queue in layout.queues:
        lines.append(f"{queue.qid},{queue.qsize},{queue.workers}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_qconf(layout: Layout, path: Path) -> None:
    """Analysis config in the Objective A column order."""
    lines = [QCONF_HEADER]
    for queue in layout.queues:
        lines.append(f"{queue.qid},{queue.qsize},{queue.workers},wait,1,,,")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_host_spec(layout: Layout, path: Path) -> None:
    """JSON the hosted pair loads. One record per logical queue."""
    payload = {
        "size": layout.size,
        "replicas": layout.replicas,
        "search_base": layout.search_base,
        "regions": list(layout.regions),
        "modes": list(layout.modes),
        "bands": list(layout.bands),
        "admit_qid": layout.admit_qid,
        "ready_qid": layout.ready_qid,
        "allocate_qid": layout.allocate_qid,
        "ready_fail_p": layout.ready_fail_p,
        "max_ready_visits": layout.max_ready_visits,
        "seed": layout.seed,
        "single": layout.single_search,
        "queues": [
            {
                "qid": queue.qid,
                "number": queue.number,
                "stage": queue.stage,
                "server": queue.server,
                "qsize": queue.qsize,
                "workers": queue.workers,
                "next": queue.next_qid,
                "bracket": queue.bracket,
                "replica": queue.replica,
            }
            for queue in layout.queues
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_bracket_layout(layout: Layout, destinations: dict[str, str], path: Path) -> None:
    """Route and ready-retry map. Values are ``host:port,workers`` or a file path."""
    payload = {
        "replicas": layout.replicas,
        "search_base": layout.search_base,
        "regions": list(layout.regions),
        "modes": list(layout.modes),
        "bands": list(layout.bands),
        "single": layout.single_search,
        "destinations": destinations,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def search_worker_total(layout: Layout) -> int:
    return sum(queue.workers for queue in layout.search_queues)


def backend_for(layout: Layout) -> str:
    """``process`` through 80 queues, ``hosted`` above that."""
    if len(layout.queues) <= PROCESS_QUEUE_LIMIT:
        return "process"
    return "hosted"


def main(argv: list[str] | None = None) -> int:
    """Print the expanded queue count for one size pattern."""
    import argparse

    parser = argparse.ArgumentParser(description="Expand a matchmaking size pattern.")
    parser.add_argument("size", choices=("small", "medium", "large", "very-large"))
    args = parser.parse_args(argv)
    pattern = load_pattern(Path(__file__).resolve().parent / args.size / "pattern.json")
    layout = build_layout(pattern)
    print(
        f"{layout.size}: {len(layout.queues)} queues "
        f"({len(layout.search_queues)} search, backend {backend_for(layout)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
