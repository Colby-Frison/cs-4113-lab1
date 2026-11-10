# Small study: methods and requirement mapping

## Scope and source questions

This study models **two queues**, within the requested 2–10-queue band, using the
food-delivery application and its C++/gRPC server functions. Each scenario has
1,000 orders per run (the lower edge of the Lab 1 small-workload range), repeated
for seeds 1, 2, and 3. These are actual asynchronous wall-clock executions,
not analytically fabricated data or a discrete-time simulator.

The requirements were checked against:

- `documentation/DOS Lab1.pdf`, slide 4: USE, latency, throughput, long-job traces.
- The same deck, slide 11, “B.XX. Modelling and Analysis”: problem mapping,
  configuration, workload, interpretation, scale, sensitivity, practical lessons.
- `CS5113/Lab 0/lab0/Documentation/Resources/Queue Lab.pdf`, slide 26,
  “D. Single Queue Modelling and Analysis”: the questions referenced in the task.

The current task explicitly asks for 2–10 queues, so this report applies those
questions to two serial queues. It does not claim completion of the medium,
large, or very-large study requirements in the slides.

## Problem and queueing model

Restaurant r0 must prepare and deliver arriving customer orders. Each order
competes for preparation workers and then for available couriers. An order can
be rejected at either finite pending queue. A pickup rejection is particularly
wasteful because its food has already been prepared.

```mermaid
flowchart LR
    G[Python generator: customer orders] --> Q0[q0: pending preparation, capacity 64]
    Q0 -->|gRPC| K[20 C++ kitchen workers: task 0]
    K -->|gRPC| Q1[q1: pending pickup, capacity 64]
    Q1 --> D[Python stateful dispatch: reserve available courier]
    D -->|gRPC| C[C++ courier workers: task 1]
    C --> S[Delivered-order CSV sinks]
    C -. successful reply releases courier .-> D
```

q0 models the kitchen backlog; q1 models prepared orders awaiting delivery.
Both pending queues use FIFO order. Parallel service can reorder completions.
The dispatcher holds one reservation per busy courier and releases it only after
successful sink output and RPC completion. Failures abort the experiment rather
than treating incomplete work as delivered.

Requested first-queue interarrival gaps are exponential, and service times are
general rather than exponential; q0 is approximately an M/G/c system. q1 sees
kitchen departures, which need not form a Poisson process. Finite buffers,
transport, and scheduling mean this is not an exact Jackson network or M/M/c
model. The goal is empirical analysis of the implemented network.

## Files, implementation, and orchestration

`study.json` is the experiment specification. `run_study.py` loads it and calls
`implementation/obj-b.food-delivery/run_grpc.py`. That runner launches one serial
C++ server process per kitchen/courier, waits for loopback gRPC readiness, and
starts the Python generator and queue/dispatch threads. It then drains every
accepted job, closes gRPC channels, and terminates/reaps idle C++ processes.
Scenarios run sequentially to avoid competing with one another.

The source protocol is Objective A's `job.proto`, `queue_to_server.proto`, and
`server_to_queue.proto`. The CSV columns remain `Job Id,arrival time,Tasks`.
Each order has a unique ID, an original generation timestamp, preparation task 0,
and delivery task 1. Each sink preserves that CSV row exactly.

Each `runs/<scenario>/seed-N/` directory contains the exact `config.json`,
`jobs.csv`, queue logs, C++ worker logs, nonempty diagnostic stderr files,
final courier registry, delivered sinks, and normalized `analysis/`
events/qmet/qconf/per-order metrics/summary. Historical readiness-port files and
empty diagnostic logs were removed during cleanup. The application’s original
FIFO runner is not used in this experiment.

## Workload decisions

- Independently sampled exponential requested gaps: `arrival_mean=0.004` model
  seconds, multiplied by `time_scale=2`, so nominal gap is 0.008 real seconds
  (125 orders/s).
- Uniform item counts 1–4. Preparation draws a uniform base 0.02–0.04 seconds,
  multiplies by item count, and then by 2. Mean requested preparation is
  0.03 × 2.5 × 2 = **0.15 seconds**.
- Uniform near/mid/far zones with travel multipliers 1/2/3. Delivery draws a
  uniform base 0.04–0.06 seconds, applies distance and time scale. Mean requested
  delivery is 0.05 × 2 × 2 = **0.20 seconds**.
- Waits are rounded to 0.001 seconds by the application before execution.
- Independent arrival and attribute RNG streams use seed and seed+1.
  Paired scenario runs use the same seed. SHA-256 checks verify identical order
  IDs and requested task durations for each paired seed across all seven sets.
- The actual interarrival intervals include generator work, scheduler delays,
  and logging/dispatch contention. They are **not identical wall-clock replay**.
  Measured offered rates are reported alongside nominal demand. This limits
  causal precision when differences between scenarios are small.

These are synthetic accelerated service times, not measured restaurant data.
The 2× time scale keeps service durations well above millisecond timestamp
resolution. Mean nominal kitchen capacity is 20/0.15 ≈ 133.3 orders/s. Courier
capacity is c/0.20 = 5c orders/s before scheduling and transport overhead. Thus
20 couriers nominally deliver 100 orders/s, and the requested arrival rate is
intentionally near/above that capacity. Actual offered load must still be checked.

## Exactly seven sensitivity scenarios

Only the **courier worker pool** changes; kitchens and both capacities stay fixed.
The percentages refer to that selected resource, not all workers combined.
Twenty baseline couriers let each requested percentage map to an exact integer.

| Set | Couriers | Change in courier resource | Kitchens | Total workers | Change in total workers |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline | 20 | 0% | 20 | 40 | 0% |
| couriers-plus25 | 25 | +25% | 20 | 45 | +12.5% |
| couriers-plus50 | 30 | +50% | 20 | 50 | +25% |
| couriers-plus100 | 40 | +100% | 20 | 60 | +50% |
| couriers-minus10 | 18 | −10% | 20 | 38 | −5% |
| couriers-minus20 | 16 | −20% | 20 | 36 | −10% |
| couriers-minus30 | 14 | −30% | 20 | 34 | −15% |

Each has three runs, 1,000 jobs per run, 64 pending slots per queue, identical
requested workload settings, and paired seeds 1/2/3. `runs/schedule.json` records the
predefined run order (baseline seed 1 first, then shuffled scenario order within
seed blocks). Seed 1 was selected in advance for each set's six main plots.
The report uses all seeds; no run or trace was selected for favorable results.

## Main metrics and six plots per set

All event timestamps are converted to elapsed milliseconds by the existing
adapter. Main plots use seconds and 0.5-second bins; the final partial bin uses
its actual duration. Reconstructed state is piecewise constant between events.
Multiple changes in the same millisecond use the final state at that timestamp.

1. **utilization.png:** for each queue, integrate busy workers over the bin and
   divide by configured workers × bin duration. “Busy” is `ENTERS-SERVER` through
   `EXITS-SERVER`; it excludes downstream output/RPC completion time during which
   a courier reservation may still be occupied. High U is not automatically good.
2. **saturation.png:** integrate waiting-order count over the bin and divide by
   bin duration. Waiting is admission through service entry. It can include a
   job already reserved or in transit to its worker, not just the pending deque.
3. **errors.png:** queue-full rejections per second for q0 and q1. Error impulses
   are counted once; each set also reports losses as a fraction of generated jobs.
4. **throughput.png:** successful final sink departures per second, with actual
   mean offered rate as a reference line. Overall throughput includes warm-up
   and drain, from first generated job to last recorded event.
5. **latency.png:** median and P10–P90 generation-to-delivery latency of orders
   completing within each bin. Empty bins have no latency estimate. Dropped
   orders are excluded from latency but reported in errors/loss tables.
6. **longest-jobs.png:** the three delivered jobs with greatest end-to-end latency,
   plotted on their real timeline. Pale bars are queue wait; solid bars are
   kitchen/delivery service. Thin connecting lines include handoff/output gaps.
   Exact IDs and phase durations appear in `longest-job-breakdown.csv`.

The comparison chart shows the arithmetic mean and min–max across three runs,
not a confidence interval. Percentage changes are first calculated against the
same-seed baseline, then averaged across the three paired seeds. The reported
P95 is the mean of the three within-run P95s, not a pooled quantile. Small timing
differences may be scheduling noise. No steady-state or statistical-significance
claim is made for these finite runs.

## Accessory-tool demonstration

Every set's `tools/commands.log` records the actual commands and outputs. Main
plot files are separate from these supporting tool demonstrations.

| Existing tool | Saved evidence per set |
| --- | --- |
| filter-by-queue | `tools/q0-qmet.csv`, `tools/q1-qmet.csv` |
| filter-by-job | `tools/longest-jobs.log` selected by end-to-end latency |
| derive-event-metrics | `tools/emetrics.csv` (per-queue visits) |
| derive-sys-metrics | `tools/sysmetrics.csv` (500 ms departure bins) |
| compute-use | `tools/quse.csv` from `tools/qmet-10ms.csv` |
| plot-use | `tools/use.png` |
| plot-sys-metrics | `tools/system.png` |
| plot-derive-event-metrics | `tools/visit-hist.png`, `tools/visit-scatter.png` |
| create-event-trace | `tools/longest-event-trace.png` |
| pretty-print-job-trace | `tools/longest-jobs-pretty.log`, `tools/all-events-pretty.log` |

The shared USE tool averages rows, not elapsed time. Its input here is uniformly
sampled at 10 ms, so windows of 10/100/1000 rows correspond to 0.1/1/10 seconds.
This is a sampled approximation; main USE plots and reported overall utilization
use the exact event-state integral at available millisecond resolution. Error
counts are allocated once to right-closed 10 ms buckets before calling compute-use.

Native system-tool throughput values are **counts per 500 ms bin**, and its
latencies are milliseconds. The main plots explicitly convert throughput to
orders/s and latency to seconds. The event-tool field named `response-time` is
per-queue waiting, so `turnaround-time` is used for visit distributions. Main
customer latency comes from the food app's generation-to-sink accounting.

The event tool's stderr `dropped` counter only counts a rejection after an opened
queue visit (`ARRIVE-VIA`). Our full queues reject before admission, so that
counter can say zero even when pickup orders were rejected. The raw
`ERROR QUEUE-FULL` events, qmet error counts, and food-app outcome reconciliation
are the loss evidence. The system tool correctly notes source arrivals that
never reached a sink; the study verifies that these are known drops, not
incomplete jobs.

## Validation, scope limits, and reproduction

`results/validation.json` checks the number of runs, 42 required main plots, order
accounting, identical paired task workloads, and drained courier registries.
State checks reject negative counts, service counts above configured workers,
nonempty final queues, or disagreement between queue errors and dropped orders.
`runs/provenance.json` records the code/binary hashes, Git revision, Python/gRPC versions,
and host platform. Raw logs preserve the evidence behind every derived metric.

This local experiment varies couriers only. It does not test network failures,
multiple restaurants, real geography, return trips, packaging, route batching,
remote deployment, or larger queue counts. More buffering might trade fewer drops
for longer waits, but that hypothesis is not tested here. Host scheduling and
snapshot/log I/O are part of observed timing; no claim is made about physical
restaurant throughput or C++ speedup over Python.

From the repository root, after following the app's `cpp/README.md` build/setup:

```bash
# Collect missing runs and create reports; existing successful runs are checked/reused.
MPLCONFIGDIR=/tmp/food-delivery-mpl \
implementation/obj-b.food-delivery/.venv/bin/python \
  analysis/simple/obj-b-food-delivery/small/run_study.py --collect --report

# Rebuild plots/tables from saved raw evidence without running gRPC services.
MPLCONFIGDIR=/tmp/food-delivery-mpl \
implementation/obj-b.food-delivery/.venv/bin/python \
  analysis/simple/obj-b-food-delivery/small/run_study.py --report
```

For a fresh independent collection, use a copy of this study directory without
its run directories and provenance file. The runner refuses to overwrite a
nonempty run directory lacking a valid summary. The collection requires localhost
TCP permission; rebuilding plots requires only local file access.

An independent audit is reproducible with `python3 analysis/simple/obj-b-food-delivery/small/validate_study.py`;
its output is `results/audit.json`. It also checks raw sink row identity, sampled error counts,
binned departure counts, and longest-job selection.
