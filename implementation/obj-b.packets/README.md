# Packet routing network

Objective B application: IPv4 packets forwarded across a network of routers, modeled as a network of queues. The target is a system of 1000 queues, with the topology and routing tables generated automatically.

This tree will sit on the [Objective A framework](../obj-a/README.md). This document is the problem statement and the model. [architecture.md](architecture.md) is the design, and [What is here](#what-is-here) lists the code. The optimization goal and its targets have not been settled by the group.

## The problem

A packet enters the network at one router and has to reach the router that serves its destination address. Each router on the way looks up the destination in its routing table and forwards the packet to the next hop. A router handles a limited number of packets at a time. Packets it cannot handle yet wait in its buffer, which adds latency. When the buffer is full, the packet is dropped, which is loss.

Given a topology, routing tables, and a traffic pattern, the study asks:

- How much latency does the network add, end to end and at each router?
- How many packets are lost, and where?
- Which routers are the bottlenecks, and are they the ones the topology suggests?
- How much forwarding capacity is needed, where should it go, and what happens when it is cut or added?

One thing makes this more than a larger wait-job study. Objective A's server function picks an outgoing edge at random, so it cannot send a packet anywhere in particular. This application needs a server function that routes on the destination address.

At 1000 queues, nothing can be configured by hand. Topology, addresses, routing tables, and process configuration all have to come from a generator.

## Mapping to queues

| Real network | Queueing model |
| --- | --- |
| Packet | Job. Carries a destination IPv4 address and a TTL. |
| TTL | Reduced by one at each router that forwards the packet. A packet whose TTL runs out before it reaches its destination is dropped and logged `ERROR TTL-EXPIRED`. |
| Router | One queue system: a buffer (`queue.py`) and its server function |
| Buffer size | `--qsize`. A packet that does not fit is dropped and logged `ERROR QUEUE-FULL`. |
| Work on one packet | Three task waits: validate, lookup, and forward. `W` is their total. A packet costs its `W` at every router, because the row is forwarded unchanged. |
| Forwarding capacity | Server count $c$ on that router, giving a capacity of $c / E[W]$ packets per second |
| Routing table | Lookup in the server function, from destination address to next hop |
| Link | Directed edge from one router's server function to the next router's queue |
| Hosts sending traffic | A workload generator feeding a router's queue over a FIFO, drawing destinations from a traffic mix |
| Delivery | Exit to a sink file at the destination router |

The destination address and TTL are fields of the gRPC `Job` message. The generator cannot speak gRPC, so it writes their starting values as two extra columns in the job row, and the first queue reads them from there. After that the message is what counts: the TTL changes at each hop, while the row is forwarded unchanged. The analysis tools read logs rather than job rows, so these columns do not affect them.

This is an open network. Packets enter from outside, cross some routers, and leave. Routes are fixed once the tables are generated, so the arrival rate at router $i$ can be computed before a run by summing the traffic of every source-destination pair whose path crosses $i$:

$$\lambda_i = \sum_{(s,d)\,:\,i \in \text{path}(s,d)} \lambda_{sd} \qquad \rho_i = \frac{\lambda_i E[W]}{c_i}$$

A router keeps up only when $\rho_i < 1$. The router with the largest $\rho_i$ limits the whole network. These predictions are checks on a run: measured utilization should match $\rho_i$ until drops begin, and Little's law ($L = \lambda T$) relates a queue's occupancy to its delay.

The model leaves these out:

- **Output-link contention.** One queue per router models one forwarding bottleneck, so 1000 queues means 1000 routers. Real routers often congest on individual outgoing links. One queue per link would be more realistic, but it would spend the 1000 queues on far fewer routers.
- **Link delay.** Time on the wire is zero unless it is added to `W`.
- **Sender reaction.** Traffic is open loop. Sources do not slow down when packets are lost, and nothing is retransmitted, so overload shows up as loss rather than as TCP backing off.

Every hop between queues and server functions uses gRPC. The two exceptions are outside the routers: each generator feeds its first queue over a FIFO, and each sink is a file. gRPC matters most on the hop from a queue to its server function. On a FIFO, a queue "departs" a packet as soon as the row fits in the pipe. In one run of 100 packets in a burst, with `--qsize 5` and `W` = 0.05 s, the queue logged no drops and a peak `metric-q-size` of 0, and had finished when the server had started only 6 visits. The rest were in the pipe. `--qsize` and the saturation metric only mean something if packets wait in the buffer.

## Interpreting the metrics

| Metric | Source | Reading in this system |
| --- | --- | --- |
| Utilization | `compute-use` | Share of a router's forwarding capacity in use |
| Saturation | `compute-use`, `metric-q-size` | Packets waiting in a router's buffer |
| Errors | `ERROR QUEUE-FULL` | Congestion loss at a router |
| Per-hop queuing delay | `time-in-queue` in `derive-event-metrics` | Time waiting in one router's buffer |
| Per-hop forwarding delay | `time-in-service` in `derive-event-metrics` | Time being forwarded, about `W` |
| End-to-end latency | `derive-sys-metrics` | First arrival to delivery. P90 says more than the mean, because congestion shows up in the tail. |
| Throughput | `derive-sys-metrics` | Packets delivered per second |

`ERROR TTL-EXPIRED` and `ERROR NO-OUT-EDGE` (no route to the destination) are log lines rather than metrics. They point to a configuration fault, not congestion, and generated routing tables should never produce them.

Read together, these patterns separate the causes:

- **High utilization, growing saturation, then drops** is a real bottleneck: that router lacks capacity for its load.
- **Drops with moderate average utilization** points to bursts, or to a buffer too small for them.
- **Low utilization downstream of a dropping router** does not mean the downstream router has spare capacity. It only sees what got through, so fixing the upstream router can move the bottleneck.
- **Throughput that levels off as offered load rises** means the network has reached capacity, which should happen near the point where the largest $\rho_i$ reaches 1.
- **Latency that falls while loss rises** is not an improvement. Latency only counts delivered packets, so dropping more can make it look better. Always report latency and loss together.

## What we are optimizing

Proposed goal:

> Find the least total server capacity that keeps packet loss below $X$ and P90 end-to-end latency below $Y$, for a given topology and traffic mix.

The fixed-budget version is equivalent and matches the lab's server sweeps: given a set number of servers, find the placement that delivers the most packets within the latency limit.

$X$ and $Y$ have not been chosen. `W` is an artificial value, so $Y$ should be a multiple of zero-load latency, which is the number of routers on the path times `W`.

The settings we can change are server placement, buffer sizes, and routing policy. Three things shape how they are judged:

- **Utilization is not the goal.** Queuing delay rises sharply as $\rho$ approaches 1, so a router near full utilization is a warning, not a success.
- **Buffers trade loss for latency.** A larger buffer can reduce drops while packets wait longer, which can hide congestion without fixing it. The server sweep should be run with buffer rules held fixed.
- **Capacity placement is the main question.** The comparison is between weighting servers toward hubs and spreading them evenly. Node degree alone may be a poor guide, because load follows the paths packets take rather than the number of links a router has.

## What is here

| Path | Role |
| --- | --- |
| [architecture.md](architecture.md) | Design and diagrams |
| [workload-generator/](workload-generator/README.md) | Packet stream, and why each distribution was chosen |
| [server-monolith/](server-monolith/README.md) | A router's whole job in one server function |
| [server-stages/](server-stages/README.md) | The same job split into validate, lookup, and forward |
| [cpp/](cpp/README.md) | C++ port of all of the above server functions |
| `pk/` | Routing tables, addresses, and the visits, shared by the Python server functions |
| `build_network.py`, `spec.json` | Expands the spec into a topology, routing tables, and addresses |
| `topology/as20000102.txt` | SNAP `as-733` Autonomous System graph, 2 January 2000 |
| `run_local.py` | Runs a small network on one machine |
| `tests/` | Unit tests, whole networks, and Python and C++ compared |

The [firewall application](../obj-b.firewall/README.md) builds on this code through three hooks: `_admit` and `_reject` in a router's visit, a router factory in `pk/runtime.py`, and an optional extension in `run_local.run_network`. Without them, nothing here changes.

The [studies](../../analysis/obj-b-packets/README.md) run four models, from 10 routers to 10,200 queues, each with a one-node Slurm job for Sooner.

## Run

gRPC is required: `pip install -r implementation/obj-a/requirements.txt`. From the repository root, this runs 10 routers on one machine and prints where every packet ended up:

```text
python implementation/obj-b.packets/run_local.py --out run-monolith
python implementation/obj-b.packets/run_local.py --out run-stages --mode stages --routers 6
```

| Flag | Meaning |
| --- | --- |
| `--mode monolith\|stages` | Which router form to run |
| `--routers N`, `--sources N` | Network size and number of traffic sources |
| `--rate R`, `--duration SEC` | Total packets per second, and for how long |
| `--server-bin PATH` | Use the C++ `packet_server` |
| `--analyze` | Also run the Objective A analysis tools, into `analysis/`. Needs `numpy`, `pandas`, and `matplotlib`. |

The second command starts 36 router processes, and each Python process uses about 40 MB, so keep local runs small.

## Tests

From the repository root:

```text
python -m unittest discover -s implementation/obj-b.packets/tests -v
```

| File | What a pass means |
| --- | --- |
| `test_generator.py` | Rows are well formed, the distributions have the documented shape, and a seed repeats. |
| `test_routing.py` | Longest-prefix match, table and address files, and ports that do not collide, including across loopback addresses. |
| `test_build_network.py` | Samples are connected, tables deliver every pair, and the path check rejects loops, dead ends, and paths over the TTL. |
| `test_router.py` | Every visit's events and decisions, and that the monolith and the stages end every packet the same way. |
| `test_parity_and_network.py` | Whole networks account for every packet, and Python and C++ behave the same. Needs grpcio. The C++ cases need `PACKET_SERVER_BIN`. |
