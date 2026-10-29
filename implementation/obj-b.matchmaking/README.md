# Ranked 5v5 matchmaking

Objective B application for a live-service ranked tactical shooter. A player party asks for a match. The system checks the party, reads a hidden skill rating, sends the ticket to a regional search, waits out a ready check, and then places the party on a match host. The product name stays out of the writeup. The queueing model is what the rest of this directory implements.

This tree sits on the [Objective A framework](../obj-a/README.md). Queues are `implementation/obj-a/queue/queue.py`. Server functions live here. They import `lib` from Objective A and `mm` from this directory. The file path does not import grpc. The Small study is [studies/small/README.md](studies/small/README.md). How that runner is built, and what to copy for the next size, is [studies/README.md](studies/README.md).

## The queueing problem

A job is one party ticket, not one player and not one finished match. The CSV is the Lab 0 row (`Job Id`, `arrival time`, `Tasks` as `tid: W seconds`), so the Objective A analysis tools still parse the logs. Party size, region, mode, and skill band are stored in the job id because a job row has three columns. The grammar is in [architecture.md](architecture.md).

Each ticket flows through a network of queues. A queue buffers and can drop. A server function sleeps the wait that belongs to its stage, then forwards the original row. `W` is already scaled by the generator. Servers do not scale it again.

| Metric | Reading in this system |
| --- | --- |
| End-to-end latency | Time-to-match. First `ARRIVE-VIA` on `q-admit` through `EXITS-SERVER` on `q-allocate`. |
| Per-stage latency | One visit, `ENTERS-SERVER` to `EXITS-SERVER`. Search should be the long visit. Validate, rating, and route should be short. |
| Utilization | Wall time between `TASK-START` and `TASK-END` on that stage. |
| Saturation | `metric-q-size`, watched on search and allocate. |
| Errors | `ERROR QUEUE-FULL` is a rejected ticket: matchmaking unavailable. The producer does not block. `ERROR NO-OUT-EDGE` means a stage finished and had nowhere to send the ticket. |
| Ready-check failure | Another `ENTERS-SERVER` on the search queue for the same job id. The visit cap sends the ticket to the failed sink instead of looping. |
| Throughput | Tickets per second in `matched.csv`. Player throughput is that rate times party size. It is a derived number. |

The quantity to optimize is time-to-match together with the drop rate (`QUEUE-FULL` plus ready-check failures that exhaust the visit cap). Tickets stay inside one region and one skill band because those values pick the search queue. This pass runs a single region (`na`) and a single mode (`ranked`). Later studies multiply queues by region, mode, and skill band, and by replicas of a stage. [topology/baseline.json](topology/baseline.json) is the process list those studies and the orchestrator both start from. How a short pattern becomes process flags and a `qconf.csv` for the larger bands is in the [Objective A topology section](../obj-a/README.md#topology).

Unscaled search service has mean about $11.5$ seconds and the default interarrival mean is $14$ seconds, so one search server sees $\rho \approx 0.8$. Baseline search `qsize` is $32$. An M/M/1 queue at that load has a mean waiting line of a few tickets, so a short run does not fill the buffer at once. The search and allocate `workers` fields are $4$. That pool is the gRPC worker count, not extra processes. A later sweep that shrinks the pool by $10\%$, $20\%$, and $30\%$ has something to shrink. The file demo still uses one thread per process.

A ticket is one queue slot even when the party has five players. Allocate capacity in this model is tickets, not seats on the match host. Later writeups that talk about host capacity should keep that limit visible.

## What is here

| Path | Role |
| --- | --- |
| [architecture.md](architecture.md) | Stages, job id, routing, diagram |
| [topology/baseline.json](topology/baseline.json) | Six queues, six stage servers, ready-check policy |
| [topology/monolith.json](topology/monolith.json) | One queue and one server that sleeps every stage |
| [workload-generator/](workload-generator/README.md) | Party tickets and the six waits |
| [server-monolith/](server-monolith/README.md) | All six waits in one visit |
| [server-stages/](server-stages/README.md) | One stage per process, ready-check retry, visit cap |
| [cpp/](cpp/README.md) | The same stage server in C++, including gRPC |
| [run_demo.py](run_demo.py) | File-edge demo of the monolith and of the six stages |
| [studies/README.md](studies/README.md) | Local study runner: processes, routing, logs, and what the next size copies |
| [studies/small/](studies/small/README.md) | Small server sweep, figures, and the measured write-up |
| [orchestration/](orchestration/README.md) | One Windows command that runs the same pipeline in containers |

`runs/` is scratch output from the demo and is gitignored.

## Demo

From the repository root:

```text
python implementation/obj-b.matchmaking/run_demo.py
```

That runs the monolith, then the staged pipeline, under `implementation/obj-b.matchmaking/runs/demo`. Ordinary files are the edges, so the demo does not need FIFOs or a gRPC package. The topology's `time_scale` keeps the sleeps short. `--count` and `--time-scale` override the topology for a quicker check.

Listeners and FIFO readers still have to start before producers when a later runner switches an edge to a FIFO or to `--ingrpc` / `--outgrpc`. Drop-on-full stays inside the Objective A queue process.

## Containers

[orchestration/README.md](orchestration/README.md) is the Windows launcher. One command builds the image, starts a container per queue and per stage, and starts the workload generator. That README also says where the container logs go and how to follow them while a stack is up:

```text
python implementation/obj-b.matchmaking/orchestration/run.py
```
