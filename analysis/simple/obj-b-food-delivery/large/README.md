# Large food-delivery study: 200 queues

Completed **21 runs, 2,100,000 orders, and 42 main plots**. The audit passed with 1,802,827 delivered, 297,173 rejected, and none incomplete.

## Model and workload

There are 150 restaurant kitchen queues and 50 regional pickup queues. Each
region serves three restaurants. Baseline resources are 300 kitchen workers and
200 couriers, hosted in two C++ processes plus a Python coordinator.

```mermaid
flowchart LR
    G[Scheduled orders] --> Q[150 kitchen queues]
    Q --> K[C++ kitchen host]
    K --> P[50 regional pickup queues]
    P --> C[C++ courier host]
    C --> S[Delivered orders]
```

Queues and courier reservations remain independent despite sharing processes.
The goal is higher delivered throughput with less waiting and loss at a given
worker budget. Seven scenarios vary couriers while keeping kitchens fixed.
Each scenario uses three paired seeds and 100,000 orders per seed.

Exponential arrival gaps average 1 ms (1,000 orders/s). Uniform item counts and
near/mid/far distances produce mean preparation/delivery times of 0.15/0.20 s.
Regional demand weights repeat 3/2.5/2/1.5/1; restaurants receive 50/30/20% of
regional demand. Waiting capacities are 32 for kitchens and 64 for pickup.
Hot regions receive about 30 orders/s against baseline courier capacity of 20/s,
predicting pickup congestion. [study.json](study.json) records all allocations.

## Measured results and interpretation

Values are three-seed means. Throughput includes draining; latency excludes
rejected orders. Percentage scenarios change courier counts, not all workers.

| Scenario | Couriers | Delivered/s | Mean latency (s) | Loss (%) |
| --- | ---: | ---: | ---: | ---: |
| baseline | 200 | 817.82 | 2.388 | 13.92 |
| couriers-plus25 | 250 | 914.92 | 1.545 | 4.57 |
| couriers-plus50 | 300 | 955.58 | 0.791 | 1.72 |
| couriers-plus100 | 400 | 955.90 | 0.725 | 1.72 |
| couriers-minus10 | 180 | 754.32 | 2.802 | 19.58 |
| couriers-minus20 | 160 | 699.42 | 3.270 | 25.41 |
| couriers-minus30 | 140 | 625.59 | 3.852 | 32.14 |

![Sensitivity comparison](results/sensitivity-comparison.png)

- Hot regions are the baseline bottleneck: pickup utilization averages 98.8%,
  versus 47.5% in the quietest regions. Couriers cannot move between regions.
- At 300 couriers, pickup rejections disappear. All remaining rejections occur
  at kitchens; adding more couriers cannot resolve that capacity limit.
- Going from 300 to 400 couriers barely changes throughput, although latency
  improves slightly. Cutting to 140 couriers raises loss to 32.14%.
- The longest baseline order takes 6.893 s, about 90.8% waiting in queues.
  Its [trace](results/baseline/longest-jobs.png) separates kitchen and pickup delays.

Hot-kitchen capacity or courier redistribution would be useful next experiments;
these results do not establish a universally optimal fleet.

## Plots and measurement limits

Each row links the six main seed-1 plots. Objective A accessory figures are in
the scenario's `tools/` folder. Aggregate results use all three seeds.

| Scenario | Utilization | Saturation | Errors | Throughput | Latency | Longest jobs |
| --- | --- | --- | --- | --- | --- | --- |
| baseline | [U](results/baseline/utilization.png) | [S](results/baseline/saturation.png) | [E](results/baseline/errors.png) | [Throughput](results/baseline/throughput.png) | [Latency](results/baseline/latency.png) | [Trace](results/baseline/longest-jobs.png) |
| couriers-plus25 | [U](results/couriers-plus25/utilization.png) | [S](results/couriers-plus25/saturation.png) | [E](results/couriers-plus25/errors.png) | [Throughput](results/couriers-plus25/throughput.png) | [Latency](results/couriers-plus25/latency.png) | [Trace](results/couriers-plus25/longest-jobs.png) |
| couriers-plus50 | [U](results/couriers-plus50/utilization.png) | [S](results/couriers-plus50/saturation.png) | [E](results/couriers-plus50/errors.png) | [Throughput](results/couriers-plus50/throughput.png) | [Latency](results/couriers-plus50/latency.png) | [Trace](results/couriers-plus50/longest-jobs.png) |
| couriers-plus100 | [U](results/couriers-plus100/utilization.png) | [S](results/couriers-plus100/saturation.png) | [E](results/couriers-plus100/errors.png) | [Throughput](results/couriers-plus100/throughput.png) | [Latency](results/couriers-plus100/latency.png) | [Trace](results/couriers-plus100/longest-jobs.png) |
| couriers-minus10 | [U](results/couriers-minus10/utilization.png) | [S](results/couriers-minus10/saturation.png) | [E](results/couriers-minus10/errors.png) | [Throughput](results/couriers-minus10/throughput.png) | [Latency](results/couriers-minus10/latency.png) | [Trace](results/couriers-minus10/longest-jobs.png) |
| couriers-minus20 | [U](results/couriers-minus20/utilization.png) | [S](results/couriers-minus20/saturation.png) | [E](results/couriers-minus20/errors.png) | [Throughput](results/couriers-minus20/throughput.png) | [Latency](results/couriers-minus20/latency.png) | [Trace](results/couriers-minus20/longest-jobs.png) |
| couriers-minus30 | [U](results/couriers-minus30/utilization.png) | [S](results/couriers-minus30/saturation.png) | [E](results/couriers-minus30/errors.png) | [Throughput](results/couriers-minus30/throughput.png) | [Latency](results/couriers-minus30/latency.png) | [Trace](results/couriers-minus30/longest-jobs.png) |

USE heatmaps cover all 200 queues using time-weighted states in one-second bins.
Regional line panels show R0–R4; CSVs retain all 50 regions. Accessory USE samples
are 100 ms apart. Main latency units are seconds; native accessory latency is ms.

Offered rates were 996.30–1,006.51 orders/s. Maximum per-run P95 injection lag
was 1.062 ms, below the 10 ms threshold; no runs were flagged. Occasional delays
still occurred, so this is wall-clock execution rather than perfect replay.
Error bars show seed min–max, not confidence intervals. These synthetic waits
and the different backend prevent a direct performance comparison with medium.

## Run and inspect

See the [C++ build guide](../../../../implementation/obj-b.food-delivery/cpp/README.md) for setup.
Archiving is optional; raw log folders and verified `.tar.gz` archives both work.
With the environment active, from the repository root:

```bash
python analysis/simple/obj-b-food-delivery/large/run_study.py --report
python analysis/simple/obj-b-food-delivery/large/validate_study.py
```

The saved audit covers the original raw evidence. Raw logs are not included in
this results-only copy; reporting or repeating the full audit requires the complete
`large/runs/` directory. Results include the [measured summary](results/summary.md), plots, metrics, and
[audit record](results/audit.json). Scripts, notes, and PNGs are tracked;
raw runs and generated CSV/log/JSON files remain local and Git-ignored.
