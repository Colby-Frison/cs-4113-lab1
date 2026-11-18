#!/usr/bin/env python3
"""Expand spec.json into a network: topology, routing tables, addresses, queues.

A random walk over the Autonomous System graph picks the routers. A breadth-first
search from each destination gives every router its next hop, with ties going to
the lowest router index. Before writing anything, the build follows the path of
every source-destination pair and fails if one does not arrive within the TTL.

``monolith`` gives each router one queue, numbered the same as the router.
``stages`` gives each router three queues, ``3r`` (validate), ``3r + 1`` (lookup),
and ``3r + 2`` (forward). A table's next hop is the queue a packet enters the next
router through.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict, deque
from pathlib import Path

_APP = Path(__file__).resolve().parent
sys.path[:0] = [str(_APP), str(_APP.parent / "obj-a")]

from pk.routing import LOCAL, router_prefix, write_addresses

MONOLITH = "monolith"
STAGES = "stages"
STAGE_NAMES = ("validate", "lookup", "forward")


def load_graph(path: Path) -> dict[int, set[int]]:
    """Read an undirected edge list. Self-loops are dropped."""
    graph: dict[int, set[int]] = defaultdict(set)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#") or not line.strip():
                continue
            left, right = (int(token) for token in line.split()[:2])
            if left != right:
                graph[left].add(right)
                graph[right].add(left)
    return dict(graph)


def random_walk_sample(graph: dict[int, set[int]], size: int, seed: int) -> list[int]:
    """Walk until ``size`` nodes have been visited. The visited set is connected."""
    if not 2 <= size <= len(graph):
        raise ValueError(f"routers must be in 2..{len(graph)}")
    rng = random.Random(seed)
    node = rng.choice(sorted(graph))
    visited = {node}
    while len(visited) < size:
        node = rng.choice(sorted(graph[node]))
        visited.add(node)
    return sorted(visited)


def induced_links(graph: dict[int, set[int]], nodes: list[int]) -> list[list[int]]:
    """Adjacency by router index, keeping only links inside the sample."""
    index = {node: position for position, node in enumerate(nodes)}
    return [sorted(index[peer] for peer in graph[node] if peer in index) for node in nodes]


def next_hops_towards(links: list[list[int]], destination: int) -> list[int | None]:
    """Each router's next hop towards ``destination``, with ties to the lowest index."""
    distance: list[int | None] = [None] * len(links)
    distance[destination] = 0
    frontier = deque([destination])
    while frontier:
        router = frontier.popleft()
        for peer in links[router]:
            if distance[peer] is None:
                distance[peer] = distance[router] + 1
                frontier.append(peer)
    hops: list[int | None] = [None] * len(links)
    for router, peers in enumerate(links):
        if router == destination or distance[router] is None:
            continue
        hops[router] = next(peer for peer in peers if distance[peer] == distance[router] - 1)
    return hops


def build_tables(links: list[list[int]]) -> list[list[int | None]]:
    """``tables[destination][router]`` is that router's next hop."""
    return [next_hops_towards(links, destination) for destination in range(len(links))]


def check_paths(tables: list[list[int | None]], ttl: int) -> dict[str, float]:
    """Follow every pair's path. Fail on a loop, a dead end, or a path over the TTL."""
    routers = len(tables)
    total = longest = 0
    for destination, towards in enumerate(tables):
        for source in range(routers):
            router = source
            hops = 0
            while router != destination:
                following = towards[router]
                hops += 1
                if following is None or hops >= routers:
                    raise SystemExit(f"no loop-free path from {source} to {destination}")
                router = following
            if hops >= ttl:
                raise SystemExit(f"path from {source} to {destination} needs TTL above {ttl}")
            total += hops
            longest = max(longest, hops)
    return {"mean_hops": total / (routers * (routers - 1)), "longest_path_hops": longest}


def choose_sources(links: list[list[int]], count: int, seed: int) -> list[int]:
    """One-link routers first, topped up from the rest when there are too few."""
    rng = random.Random(f"{seed}:sources")
    edge = [router for router, peers in enumerate(links) if len(peers) == 1]
    core = [router for router, peers in enumerate(links) if len(peers) != 1]
    rng.shuffle(edge)
    rng.shuffle(core)
    return sorted((edge + core)[: min(count, len(links))])


def visit_rates(tables: list[list[int | None]], sources: list[int]) -> list[float]:
    """Visits each router gets per packet offered, with uniform destinations."""
    routers = len(tables)
    visits = [0.0] * routers
    share = 1 / (len(sources) * (routers - 1))
    for source in sources:
        for destination in range(routers):
            if destination == source:
                continue
            router = source
            while router != destination:
                visits[router] += share
                router = tables[destination][router]
            visits[destination] += share
    return visits


def allocate_servers(weights: list[float], total: int) -> list[int]:
    """Split ``total`` servers in proportion to ``weights``, with at least one each.

    Whole servers come from the largest remainders, and ties go to the lowest
    index, so the same inputs always give the same allocation.
    """
    if total < len(weights):
        raise ValueError(f"{total} servers cannot give each of {len(weights)} routers one")
    shares = [weight / sum(weights) * total for weight in weights]
    servers = [max(1, math.floor(share)) for share in shares]
    by_remainder = sorted(range(len(weights)), key=lambda i: (servers[i] - shares[i], i))
    position = 0
    while sum(servers) < total:
        servers[by_remainder[position % len(servers)]] += 1
        position += 1
    by_surplus = sorted(range(len(weights)), key=lambda i: (shares[i] - servers[i], i))
    position = 0
    while sum(servers) > total:
        router = by_surplus[position % len(servers)]
        if servers[router] > 1:
            servers[router] -= 1
        position += 1
    return servers


def queue_id(router: int, mode: str, stage: int = 0) -> str:
    return f"q{router}" if mode == MONOLITH else f"q{3 * router + stage}"


def build(spec: dict, mode: str, out: Path, hosts: list[str]) -> dict:
    graph = load_graph(_APP / spec["topology"])
    nodes = random_walk_sample(graph, spec["routers"], spec["seed"])
    links = induced_links(graph, nodes)
    tables = build_tables(links)
    stats = check_paths(tables, spec["ttl"])
    source_list = choose_sources(links, spec["sources"], spec["seed"])
    sources = set(source_list)
    visits = visit_rates(tables, source_list)
    # A total_servers budget follows predicted load. Otherwise servers follow links.
    budget = (
        allocate_servers(visits, spec["total_servers"]) if "total_servers" in spec else None
    )
    # Buffers can stay at another budget's sizes, so a sweep changes servers alone.
    buffer_budget = (
        allocate_servers(visits, spec["buffer_servers"]) if "buffer_servers" in spec else None
    )

    routers = []
    for router, peers in enumerate(links):
        servers = (
            budget[router]
            if budget is not None
            else max(1, math.ceil(len(peers) / spec["degree_per_server"]))
        )
        buffer_servers = buffer_budget[router] if buffer_budget is not None else servers
        stage_names = STAGE_NAMES if mode == STAGES else (MONOLITH,)
        routers.append(
            {
                "router": router,
                "autonomous_system": nodes[router],
                "links": peers,
                "source": router in sources,
                "servers": servers,
                "qsize": max(spec["min_qsize"], spec["qsize_per_server"] * buffer_servers),
                "visit_rate": visits[router],
                "prefix": router_prefix(router),
                "queues": {
                    name: queue_id(router, mode, stage) for stage, name in enumerate(stage_names)
                },
            }
        )

    (out / "routes").mkdir(parents=True, exist_ok=True)
    lookup_stage = STAGE_NAMES.index("lookup") if mode == STAGES else 0
    for router in range(len(links)):
        lines = []
        for destination in range(len(links)):
            hop = tables[destination][router]
            target = LOCAL if destination == router else queue_id(hop, mode)
            lines.append(f"{router_prefix(destination)}\t{target}\n")
        table_path = out / "routes" / f"{queue_id(router, mode, lookup_stage)}.tsv"
        table_path.write_text("".join(lines), encoding="utf-8")

    qids = [qid for entry in routers for qid in entry["queues"].values()]
    write_addresses(qids, hosts, out / "addresses.csv")
    queue_rows = ["qid,qsize,servers"]
    queue_rows += [
        f"{qid},{entry['qsize']},{entry['servers']}"
        for entry in routers
        for qid in entry["queues"].values()
    ]
    (out / "queues.csv").write_text("\n".join(queue_rows) + "\n", encoding="utf-8")

    topology = {
        "mode": mode,
        "spec": spec,
        "links": sum(len(entry["links"]) for entry in routers) // 2,
        **stats,
        "routers": routers,
    }
    (out / "topology.json").write_text(json.dumps(topology, indent=1) + "\n", encoding="utf-8")
    return topology


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--spec", default=str(_APP / "spec.json"))
    parser.add_argument("--mode", choices=(MONOLITH, STAGES), default=MONOLITH)
    parser.add_argument("--out", required=True)
    parser.add_argument("--routers", type=int, help="Override the spec's router count")
    parser.add_argument("--hosts", default="127.0.0.1", help="Comma-separated hosts for addresses.csv")
    args = parser.parse_args(argv)

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    if args.routers is not None:
        spec["routers"] = args.routers
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    topology = build(spec, args.mode, out, args.hosts.split(","))
    print(
        f"{len(topology['routers'])} routers, {topology['links']} links, "
        f"mean hops {topology['mean_hops']:.2f}, longest path {topology['longest_path_hops']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
