# Orchestration

One Windows command reads [../topology/baseline.json](../topology/baseline.json), builds the matchmaking image, starts one container per queue and one per stage server, and starts the workload generator. The generator's `--count`, seed, arrival, and time scale come from that JSON. This is the product pipeline (8 tickets, 4 search workers). The size studies do not use it. They start local processes from [../studies/README.md](../studies/README.md) and leave this topology file alone.

Docker Desktop has to be installed. If the engine is stopped, the launcher starts Docker Desktop and waits for it. `workers` in the topology is the gRPC thread pool inside that container, not an extra container.

## How to run

From the repository root:

```text
python implementation/obj-b.matchmaking/orchestration/run.py
python implementation/obj-b.matchmaking/orchestration/run.py --edge fifo
python implementation/obj-b.matchmaking/orchestration/run.py --continuous
python implementation/obj-b.matchmaking/orchestration/run.py --write-only
python implementation/obj-b.matchmaking/orchestration/run.py --down
python implementation/obj-b.matchmaking/orchestration/run.py --edge fifo --down
```

| Flag | Effect |
| --- | --- |
| (default) | Regenerate the manifests, build `matchmaking:local`, recreate the gRPC stack, start the generator, and wait until both sinks have one row per ticket |
| `--edge fifo` | Same processes, with a FIFO on every hop instead of gRPC |
| `--continuous` | Omit `--count`. The generator keeps writing until the stack is stopped |
| `--write-only` | Rewrite `generated/` and do not start Docker |
| `--down` | `docker compose down -v` for the selected edge. Removes the containers and the FIFO volume |
| `--kubernetes` | Build the image and `kubectl apply` `generated/k8s.yaml` |
| `--kompose` | Run `kompose convert` on the gRPC compose file, if `kompose` is on `PATH` |
| `--topology` | A different JSON file. The default is `topology/baseline.json` |

The launcher prints a sink line about every 2 seconds while it waits:

```text
sinks 6/8 matched.csv=5 failed.csv=1
Finished. Ticket rows are in ...\orchestration\runs\grpc
The containers are still up. Stop them with:
python ...\orchestration\run.py --down
```

A finite run leaves the containers up after the sinks fill, so the logs are still there to read. Stop them with `--down`. A continuous run returns as soon as the stack is healthy and prints the same stop command. The generator's restart policy is `no`, so a generator crash does not loop. Queues and servers use `unless-stopped` and stay up after the generator exits.

## Logs

Container commands do not pass `--logfile`. Each process logs the Lab 0 line to stderr. Docker keeps that stream as the container's json log until the container is removed. `--down` removes the containers, and those logs go with them. Nothing in the repository stores a copy.

Two things do survive on disk, because they are files on a bind mount of `orchestration/runs/<edge>/` at `/shared` inside the ready and allocate containers:

| Path | What it is |
| --- | --- |
| `orchestration/runs/grpc/matched.csv` | Tickets that passed ready and allocate. `fifo` uses `runs/fifo/` |
| `orchestration/runs/grpc/failed.csv` | Tickets that failed the ready check at the visit cap |
| `orchestration/runs/grpc/ready-state.txt` | Ready-check visit count per job id |

`run.py` clears the files in that directory before `compose up`. The matchmaking `.gitignore` ignores `runs/`.

The live event feed is Compose, from a second terminal, while the stack is up. The project name is inside the compose file (`matchmaking-grpc` or `matchmaking-fifo`), so `-f` is enough:

```text
docker compose -f implementation/obj-b.matchmaking/orchestration/generated/compose.grpc.yml logs -f --tail 40
docker compose -f implementation/obj-b.matchmaking/orchestration/generated/compose.grpc.yml logs -f --tail 40 q-search s-search s-ready
docker compose -f implementation/obj-b.matchmaking/orchestration/generated/compose.grpc.yml ps
```

Swap `compose.grpc.yml` for `compose.fifo.yml` when the stack was started with `--edge fifo`. One service name limits the stream. `logs -f` follows until you stop it. `--tail 40` is the backlog it prints first.

PowerShell can follow a sink file the same way. Rows appear as allocate and ready append them:

```text
Get-Content -Wait implementation\obj-b.matchmaking\orchestration\runs\grpc\matched.csv
Get-Content -Wait implementation\obj-b.matchmaking\orchestration\runs\grpc\failed.csv
```

A log line looks like the course examples. `Q_pid…` is a queue and `sv_pid…` is a server:

```text
2026-10-29 12:00:00,123 - Q_pid12@host - INFO - 1761750000.123 t1.na.ranked.mid.p1 IN q-search ARRIVE-VIA ...
```

| Service | Process |
| --- | --- |
| `fifo-hold` | Keeps `/fifos/jobs` open so the generator and `q-admit` can start in either order |
| `q-admit`, `s-validate` | Admit queue and the validate server |
| `q-rating`, `s-rating` | Rating |
| `q-route`, `s-route` | Route. `na` goes to `q-search` |
| `q-search`, `s-search` | Search |
| `q-ready`, `s-ready` | Ready check. Pass dials `q-allocate`, retry dials `q-search`, fail appends `failed.csv` |
| `q-allocate`, `s-allocate` | Allocate. The sink is `matched.csv` |
| `generator` | Writes the CSV into `/fifos/jobs` |

gRPC ports start at 5001 inside the Compose network. They are not published on the Windows host. `q-admit` has no listen port; it reads the FIFO. The first hop is always that FIFO, on both edges, because the generator only writes CSV. On the gRPC edge every later hop is gRPC. On the FIFO edge every hop is a named pipe, and `fifo-hold` keeps each pipe open so a reader does not see end-of-file when the writer closes.

Submit is not retried. The entrypoint waits until the next listen port accepts a connection before it sends. Ready-check retry is left out of that wait on purpose: `q-search` is already up by the time a ticket can reach ready, and waiting on it would cycle with the forward path. `q-admit` publishes `/fifos/admit.ready` after it has the jobs FIFO open. The generator waits for that file.

## What gets generated

`run.py` rewrites these on every launch. Edit `topology/baseline.json`, then run the launcher. Editing the generated files by hand is lost on the next launch.

| File | Role |
| --- | --- |
| `generated/compose.grpc.yml` | gRPC between each queue and the next stage. The generator still writes CSV, so the first hop is one FIFO. Ready-check retry dials `q-search` over gRPC. |
| `generated/compose.fifo.yml` | The same processes, with a FIFO per hop. |
| `generated/k8s.yaml` | The gRPC application refined for Kubernetes |

The image is `implementation/obj-b.matchmaking/orchestration/Dockerfile`. Its build context is `implementation/`, which holds both this tree and Objective A. Containers do not install `lib`. They put `implementation/obj-a` on `sys.path` the same way the local scripts do. The image tag is `matchmaking:local` with `pull_policy: never`. The launcher builds that tag once, then `compose up` without `--build`, because a parallel build of the same tag fails.

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

The cluster has to be able to pull or already hold `matchmaking:local` (`imagePullPolicy: IfNotPresent`). On Docker Desktop that is the image the launcher just built. FIFOs are files on the FIFO volume, so the pods have to land on one node. The results volume is the same kind of claim. Both requests are 1Gi. Container logs on the cluster are `kubectl logs -n matchmaking -f <pod>`, and they follow the same rule: they last as long as the pod does. The sink files live on the results claim, not in the pod log.
