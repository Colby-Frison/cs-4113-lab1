# Food-delivery orchestration

The same two-queue application runs with queues and server functions in separate
containers. The baseline has two kitchen workers and three couriers. Both queues
reuse the existing dispatcher to reserve a worker until its service completes.

```text
Generator -> kitchen queue -> kitchen containers -> pickup queue -> courier containers -> sinks
```

FIFO mode uses Python server functions and request/reply named pipes on a shared
Docker volume. gRPC mode uses the existing C++ server and Objective A protobufs
on every hop. Worker replies keep FIFO buffering from bypassing dispatch state.

## Docker Compose

Start Docker Desktop, then run from the repository root:

```bash
python3 implementation/obj-b.food-delivery/orchestration/generate.py
sh implementation/obj-b.food-delivery/orchestration/generated/run.sh fifo
sh implementation/obj-b.food-delivery/orchestration/generated/run.sh grpc
```

The script builds one image, waits for the queues and servers, generates the
configured orders, runs analysis, and removes its containers and FIFO volume.
Results remain in `orchestration/runs/<transport>/run/`: workload, queue/server
logs, sink CSVs, dispatcher state, and Objective A-compatible `analysis/` files.
Move that `run/` folder aside before repeating a run; existing results are never
overwritten. `compose.log` contains the latest container console logs.

Use `--config path/to/config.json` to change the existing baseline settings and
`--out path/to/generated` to place manifests beside a separate `runs/` directory.
`--image registry/name:tag` changes the image name in all generated files.
The container deadline is 120 seconds, intended for a small orchestration demo.

## Kompose and Kubernetes

With [Kompose installed](https://kompose.io/installation/), regenerate **all** files:

```bash
python3 implementation/obj-b.food-delivery/orchestration/generate.py --kompose
```

`generated/kompose.json` is the actual Kompose conversion, combined into a List.
`generated/kubernetes.json` refines those converted containers. JSON manifests
are accepted by Compose and Kubernetes. Regenerate with `--kompose` whenever you
change the configuration or image for Kubernetes.

Kompose does not preserve Compose's startup dependencies or turn a host results
folder into shared storage. The refined application uses:

- A **Job** for one finite run, with a 180-second deadline and no automatic retries
- An **init container** to prepare a fresh run before any queues or servers start
- A **ConfigMap** for the workload and worker settings
- One **persistent volume claim** for results, shared by containers in the same Pod
- **Readiness probes** and **CPU/memory requests and limits**

The separate containers share one Pod and use distinct localhost gRPC ports.
This keeps storage and scheduling simple. No automatic scaling or worker restart
is enabled because queue and assignment state lives in memory.

With an active cluster, a default storage class, and the image available on its
nodes (or pushed to the registry specified by `--image`):

```bash
kubectl apply -f implementation/obj-b.food-delivery/orchestration/generated/kubernetes.json
kubectl wait -n food-delivery --for=condition=complete job/food-delivery --timeout=180s
kubectl logs -n food-delivery job/food-delivery -c generator
```

The `food-results` claim retains raw logs and analysis after the Job finishes.
To rerun, first copy out the results and move/remove its `run/` directory using a
Pod that mounts that claim, then delete the old Job and apply the manifest again.
The initialization guard prevents accidentally mixing experiments.

References: [Compose startup dependencies](https://docs.docker.com/compose/how-tos/startup-order/),
[Kompose conversion](https://kompose.io/conversion/),
[Kubernetes Jobs](https://kubernetes.io/docs/concepts/workloads/controllers/job/).
