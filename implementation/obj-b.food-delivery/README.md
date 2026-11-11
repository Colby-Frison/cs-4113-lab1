# Food delivery: preparation and courier dispatch

Objective B models customer orders competing for kitchen workers and couriers.
The goal is higher delivered throughput, shorter waits, and fewer rejected orders
with a limited worker budget. It uses Objective A's job formats and analysis tools.

## Queueing model

One order is one job with two tasks: preparation (0) and delivery (1).
Monolithic mode uses one queue and workers that perform both tasks. Staged mode
separates kitchen and courier resources:

```mermaid
flowchart LR
    G[Order generator] --> Q0[Kitchen queue]
    Q0 --> K[Kitchen workers]
    K --> Q1[Pickup queue and dispatcher]
    Q1 --> C[Couriers]
    C --> S[Delivered orders]
    C -. release courier .-> Q1
```

Kitchen processing is stateless. The dispatcher tracks each courier's availability,
assigned order, and completed count, preventing simultaneous assignments.
Regional studies use one kitchen queue per restaurant and one pickup queue per region.

| Metric | Meaning |
| --- | --- |
| Utilization | Fraction of worker capacity busy |
| Saturation | Orders waiting for service |
| Errors/loss | Orders rejected by full queues |
| Throughput | Delivered orders per second, including drain time |
| Latency | Generation-to-delivery time for delivered orders |

Read latency alongside loss: rejecting more orders can make delivered-only latency
look better. Service durations are synthetic waits, not real restaurant measurements.

## Run

From the repository root, on macOS or Linux with Python 3.10+:

```bash
python3 implementation/obj-b.food-delivery/run_demo.py --mode stages --count 20
python3 implementation/obj-b.food-delivery/run_demo.py --mode monolith --count 20
```

Settings are in [topology/baseline.json](topology/baseline.json). Use `--config`
for another configuration and `--workdir` for a fresh output directory.
For C++ runs and study dependencies, follow the [build guide](cpp/README.md).

## Workload

`o42.r0.far.i3` means order 42, restaurant 0, far zone, and three items.
The generator uses exponential arrival gaps to model independent arrivals.
Item counts (1–4) and zones (near/mid/far) are uniform; preparation takes
0.02–0.04 seconds per item, and delivery takes 0.04–0.06 seconds multiplied by
1/2/3 for distance. These simple distributions provide varied service demands.

`time_scale` scales arrival and service durations. Task waits use three decimal
places. Seeds reproduce requested workloads, though actual scheduling may vary.

```bash
python3 implementation/obj-b.food-delivery/workload-generator/workload_generator.py \
  --count 20 --seed 1 --arrival-mean 0.04 --out /tmp/orders.csv
```

Omitting `--count` from the standalone generator produces orders continuously.

## Files and runners

| Location | Purpose |
| --- | --- |
| `workload-generator/` | Specialized order generator |
| `server-monolith/`, `server-stages/` | Combined and decomposed Python services |
| `fd/` | Workload, service operations, dispatch state, and analysis |
| `cpp/` | C++ server functions and shared host |
| `run_demo.py` | Python/FIFO demonstration |
| `run_grpc.py` | Monolithic or staged C++/gRPC demonstration |
| `run_network.py` | Regional network with separate worker processes |
| `run_hosted.py` | Regional network using two shared C++ hosts |
| [orchestration/](orchestration/README.md) | FIFO/gRPC containers, Compose, and Kubernetes |

Runs contain `jobs.csv`, configuration, kitchen/courier/queue logs, delivered-order
sinks, dispatcher snapshots, and `analysis/` outputs compatible with Objective A.
Full queues cause recorded drops; unaccounted orders or service failures fail the run.
FIFO transport adds buffering, so its queue setting is not a bound on total backlog.

## Studies and tests

- [Small: 2 queues](../../analysis/simple/obj-b-food-delivery/small/README.md)
- [Medium: 20 queues](../../analysis/simple/obj-b-food-delivery/medium/README.md)
- [Large: 200 queues](../../analysis/simple/obj-b-food-delivery/large/README.md)
- [Very-large: 2,000 queues, one seed](../../analysis/simple/obj-b-food-delivery/very-large/README.md)

All four studies are complete, each with seven scenarios and six main plots per
scenario. The very-large study uses one seed; the other three use three seeds.
Regional studies share reporting and archive helpers in `study_tools/`.
Archiving is optional: it compresses raw logs after verification; reports accept
both folders and archives. Generated runs and build binaries are Git-ignored.

```bash
python3 -m unittest discover -s implementation/obj-b.food-delivery/tests -v
```

C++ integration tests require built binaries and Python gRPC dependencies;
otherwise those tests skip. The suite covers services, dispatch, routing,
overload, deadlines, the 200-queue host, and archive integrity.
