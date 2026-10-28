# Orchestration

One Windows command builds the matchmaking image, starts a container for every queue and every stage server, and starts the workload generator. The generator's `--count`, seed, and time scale come from [../topology/baseline.json](../topology/baseline.json).

```text
python implementation/obj-b.matchmaking/orchestration/run.py
```

Docker Desktop has to be installed. If the engine is stopped, the launcher starts Docker Desktop and waits for it. The default edge is gRPC. Each process is one container. `workers` in the topology is the gRPC pool on that process.

Finished tickets are appended to `orchestration/runs/grpc/matched.csv`. Ready-check failures that hit the visit cap go to `failed.csv` in the same directory. The launcher waits until those two files together contain one row per generated ticket, then leaves the containers up. Stop them with:

```text
python implementation/obj-b.matchmaking/orchestration/run.py --down
```

`--continuous` omits `--count`, so the generator keeps writing tickets until the stack is stopped. `--edge fifo` uses named pipes for every hop instead of gRPC. `--write-only` regenerates the manifests and does not start anything.

## What gets generated

| File | Role |
| --- | --- |
| `generated/compose.grpc.yml` | gRPC between each queue and the next stage. The generator still writes CSV, so the first hop is one FIFO. Ready-check retry dials `q-search` over gRPC. |
| `generated/compose.fifo.yml` | The same processes, with a FIFO per hop. A holder container keeps every FIFO open so the readers and writers can start in any order. |
| `generated/k8s.yaml` | The gRPC application refined for Kubernetes |

The image is `implementation/obj-b.matchmaking/orchestration/Dockerfile`. Its build context is `implementation/`, which holds both this tree and Objective A. Containers do not install `lib`. They put `implementation/obj-a` on `sys.path` the same way the local scripts do.

The admit queue is the only process fed by a file. It publishes `/fifos/admit.ready` after it has that FIFO open. The generator waits for the file, and every sender waits until the next listen port accepts a connection. Ready-check retry is not part of that wait: `q-search` is already up by the time a ticket can reach ready, and waiting on it would cycle with the forward path. Submit is not retried, so a listener that is late would drop the ticket.

## Kubernetes

The refined manifest adds a `matchmaking` namespace, a Service per listener, TCP startup and readiness probes, CPU and memory requests, a recreate strategy, a FIFO volume shared by the holder, the admit queue, and the generator, a results volume for the two sinks, and a Job for a finite generator run. A continuous run is a Deployment instead of a Job.

Kompose's translation of the Compose file is the mechanical step. With `kompose` on `PATH`:

```text
python implementation/obj-b.matchmaking/orchestration/run.py --kompose
```

That writes `generated/kompose/`. Apply the refined manifest, after building the image, with:

```text
python implementation/obj-b.matchmaking/orchestration/run.py --kubernetes
```

The cluster has to be able to pull or already hold `matchmaking:local` (`imagePullPolicy: IfNotPresent`). On Docker Desktop that is the image the launcher just built. FIFOs are files on the FIFO volume, so the pods have to land on one node. The results volume is the same kind of claim. Both requests are 1Gi.
