# Medium food-delivery study: model and methods

## System and scope

This is the proposed **20-queue system**: 15 restaurant preparation queues and
five regional pickup queues. Each restaurant has two C++ kitchen workers. Three
restaurants share each region's stateful dispatcher and C++ courier pool.
There is no courier sharing between regions.

```mermaid
flowchart LR
    G[Live customer-order generator] --> Q[15 restaurant queues: q0–q14]
    Q --> K[Two C++ kitchen workers per restaurant]
    K --> P[Five regional pickup queues: q15–q19]
    P --> D[One stateful courier registry per region]
    D --> C[C++ courier pools]
    C --> S[Delivered-order sinks]
    C -. release on successful completion .-> D
```

| Region | Restaurants / kitchen queues | Pickup queue | Requested demand share |
| --- | --- | --- | ---: |
| 0 | r0–r2 / q0–q2 | q15 | 30% |
| 1 | r3–r5 / q3–q5 | q16 | 25% |
| 2 | r6–r8 / q6–q8 | q17 | 20% |
| 3 | r9–r11 / q9–q11 | q18 | 15% |
| 4 | r12–r14 / q12–q14 | q19 | 10% |

Within each region, the three restaurants receive 50%, 30%, and 20% of that
region's requested demand. For example, r0 receives 15% of all orders and r2
receives 6%. The ID `o42.r7.far.i3` still means order 42, restaurant 7, far-distance
zone, three items. Restaurant 7 routes to region 2; “far” is a distance multiplier,
not a regional identifier. The configuration explicitly supplies this mapping.

The resource problem is capacity mismatch: demand is uneven, kitchens are
restaurant-specific, and the baseline courier fleet is evenly divided among
regions. A popular restaurant can saturate its kitchen while a quieter restaurant
has idle workers. Regional pickup backlogs affect all three restaurants sharing
that fleet, and a spare courier in another region cannot help.

This study addresses the requested **10–100 queue** medium band, using 10,000
orders per run. It does not claim a large or very-large system study.

## Implementation and lifecycle

`implementation/obj-b.food-delivery/run_network.py` orchestrates the network.
It launches one serial C++ process per worker, starts the Python queue/dispatch
threads, waits for gRPC readiness, and then generates orders live. All gRPC
communication uses loopback on this host. The existing Objective A protocol is
unchanged. `cpp/server.cpp --restaurants` restricts each kitchen to its restaurant
and each courier to the restaurants in its region; the default remains r0 for
the original small application.

Restaurant q0–q14 pending deques are bounded at **32** orders each. Regional
q15–q19 pending deques are bounded at **64** each. Full queues reject the new
order and log `ERROR QUEUE-FULL`. Pickup rejection wastes already-completed
preparation. FIFO is preserved within each pending deque; service completion order
can differ because workers and job durations differ.

Each regional dispatcher reserves an available courier before submitting its
RPC, and releases it only after successful sink output and RPC completion.
The runner waits for all kitchens to finish before closing regional admission,
then drains every courier. Deadline, transport, process, or accounting failures
fail a run. The final registry is an inspection snapshot, not recovery storage.

Every accepted order must appear exactly once in its region's sink or have one
queue-full rejection. Analysis checks unchanged CSV contents, routing through the
correct kitchen and pickup queue, event/sink agreement, and drained registries.
No order is silently counted as delivered after a downstream rejection.

## Workload and analytical expectations

- 10,000 orders per run; seeds **1, 2, 3** paired across all scenarios.
- Requested exponential interarrival mean: 0.003 model seconds, multiplied by
  `time_scale=0.5`, giving a nominal mean gap of **0.0015 real seconds** or
  **666.7 requested orders/s**.
- Item count is uniformly 1–4. Preparation is uniform 0.02–0.04 seconds per item,
  multiplied by 0.5: mean **0.0375 real seconds** per preparation task.
- near/mid/far are uniform, with delivery-distance multipliers 1/2/3. Delivery is
  uniform 0.04–0.06 seconds × distance × 0.5: mean **0.05 real seconds**.
- Wait durations are rounded to milliseconds before execution. Arrival,
  service-attribute, and restaurant-choice RNGs use seed, seed+1, and seed+2.
- The same-seed restaurant IDs, distances, item counts, and requested task
  durations must have matching hashes across the seven scenarios.

The actual offered rate is lower than the nominal rate because live sleeps,
generator work, scheduling, logging, and gRPC contend on one computer. Requested
random gaps are reproducible; actual arrival timestamps are **not exact replay**.
Per-run and regional measured offered rates are retained, and conclusions must
not interpret small throughput differences as a pure resource effect.

Before overhead, each two-worker kitchen offers about 2/0.0375 = **53.3 orders/s**.
A four-courier regional pool offers about 4/0.05 = **80 orders/s**. Region 0 and
its busiest restaurant have the highest offered demands, despite equal baseline
worker counts elsewhere. This predicts possible kitchen and regional bottlenecks,
which the observed USE and loss locations test. These are synthetic accelerated
service times, not real restaurant measurements.

Requested source arrivals are approximately Poisson before scheduling overhead;
service distributions are general. Pickup arrivals are kitchen departures and
need not be Poisson. Finite buffers and RPC transfers make a simple exact M/M/c
or Jackson-network interpretation inappropriate.

## Seven sensitivity sets

Thirty kitchen workers, all queue capacities, routes, workload parameters, and
requested demand shares stay fixed. Only total regional courier capacity changes.
For reductions, whole-worker allocations use equal quotas with largest remainders;
ties favor lower region IDs. This is a predefined integer allocation rule, **not**
a demand-optimized reassignment. Percentages are exact across the courier fleet,
not necessarily within each region.

| Set | Regional couriers R0/R1/R2/R3/R4 | Total couriers | Courier change | Total workers | Total-worker change |
| --- | --- | ---: | ---: | ---: | ---: |
| baseline | 4 / 4 / 4 / 4 / 4 | 20 | 0% | 50 | 0% |
| couriers-plus25 | 5 / 5 / 5 / 5 / 5 | 25 | +25% | 55 | +10% |
| couriers-plus50 | 6 / 6 / 6 / 6 / 6 | 30 | +50% | 60 | +20% |
| couriers-plus100 | 8 / 8 / 8 / 8 / 8 | 40 | +100% | 70 | +40% |
| couriers-minus10 | 4 / 4 / 4 / 3 / 3 | 18 | −10% | 48 | −4% |
| couriers-minus20 | 4 / 3 / 3 / 3 / 3 | 16 | −20% | 46 | −8% |
| couriers-minus30 | 3 / 3 / 3 / 3 / 2 | 14 | −30% | 44 | −12% |

There are **21 measured runs, 210,000 generated orders**. Scenarios run sequentially.
Baseline seed 1 runs first; other scenario orders are reproducibly shuffled within
seed blocks and saved in `runs/schedule.json`. Seed 1 is the preselected representative
for each set's six plots. Aggregate comparisons use every seed, not just that
representative run. These finite runs include warm-up and drain.

## Six main plots per scenario

1. **utilization.png:** 20-row heatmap of time-weighted busy-worker fraction.
2. **saturation.png:** 20-row heatmap of time-weighted waiting orders.
3. **errors.png:** 20-row heatmap of queue-full rejections per second.
4. **throughput.png:** overall delivered orders/s and the five regional rates.
5. **latency.png:** overall median/P10–P90 delivered latency, plus regional medians.
6. **longest-jobs.png:** phase traces for the three greatest end-to-end delivered
   latencies, with queue wait and service shown separately for the actual route.

The 15 kitchen rows and five regional rows are separated visually in each USE
heatmap. Plot intervals are one second; the final partial bin uses its true
width. State is integrated piecewise between events at the logged millisecond
resolution. At equal timestamps, the final reconstructed state determines the
next interval. Error impulses are counted once. Heatmap limits are shared where
possible and expand if needed to avoid clipping; check the color bar.

Utilization uses `ENTERS-SERVER` to `EXITS-SERVER`, excluding output/RPC return
time during which a reservation may remain occupied. Waiting spans admission to
service entry, so it includes reserved/in-flight orders; it can briefly exceed
the pending-deque limit. Errors at rejected admission are not waiting orders.

Latency is original generation to final sink departure for delivered orders;
drops are excluded from latency and reported separately. Overall throughput is
successful sink departures divided by the run observation interval. Regional
throughputs use that same full-network interval, so quiet regions also include
time spent draining a slower region. Empty completion bins have no latency value.

The comparison figure shows means with min–max across three seeds, not confidence
intervals. Percentage changes are computed against each same-seed baseline, then
averaged. Reported P95 is the mean of within-run P95s, not a pooled quantile.

## Accessory tools and artifacts

Every scenario's `tools/commands.log` records successful executions of all ten
existing accessory tools: filter-by-queue, filter-by-job, derive-event-metrics,
derive-sys-metrics, compute-use, plot-use, plot-sys-metrics,
plot-derive-event-metrics, create-event-trace, and pretty-print-job-trace.

The shared USE input is uniformly sampled every 10 ms; its 10/100/1000-row windows
therefore span 0.1/1/10 seconds. Main USE uses exact event-state integration rather
than event-row averaging. All 20 queues are computed; the shared USE demonstration
plots the five regional pickup queues for readability.

Native accessory latency/event plots use milliseconds. The shared system tool
uses 1000 ms bins, so its departure count per full bin is numerically orders/s;
main plots explicitly normalize the final partial bin. Its drop warning refers
to source arrivals without a final sink; accounting verifies those are known
rejections. The event tool's `dropped` counter can be zero for rejects occurring
before `ARRIVE-VIA`; use raw ERROR events and the reconciled summary for losses.

Raw runs retain source CSVs, configuration, queue/worker event logs, sinks,
nonempty stderr, final registry snapshots, and normalized analysis inputs.
Historical readiness-port files and empty diagnostic logs are omitted.
They may be stored as verified `runs/<scenario>/seed-N.tar.gz` archives to reduce local storage. The entire `runs/` directory is ignored by Git. `report_study.saved_run` transparently
reads either directory or archive. Supporting tool command logs record the paths
used at execution time; extracted temporary paths need not exist afterward.

`metrics-by-run.csv`, `metrics-by-queue.csv`, and `metrics-by-region.csv` support
the summary. `runs/provenance.json` captures collection code/binary/configuration
hashes and the host. Plotting scripts accompany the final study. The collection does not change any existing small-study evidence.

## Reproduction

From the repository root, with the application C++ binary rebuilt and its `.venv`
dependencies installed:

```bash
cmake --build implementation/obj-b.food-delivery/cpp/build -j 4
MPLCONFIGDIR=/tmp/food-delivery-mpl \
implementation/obj-b.food-delivery/.venv/bin/python \
  analysis/simple/obj-b-food-delivery/medium/run_study.py --collect
MPLCONFIGDIR=/tmp/food-delivery-mpl \
implementation/obj-b.food-delivery/.venv/bin/python \
  analysis/simple/obj-b-food-delivery/medium/run_study.py --report
```

Collection needs localhost TCP access. Replotting saved data does not start
services. A nonempty unfinished run is never overwritten. Use a clean copy of
the study without prior raw runs/provenance for a new independent experiment.

These results test one host, one workload model, and one routing/allocation
policy. They do not prove production capacity, an optimal fleet allocation,
statistical significance, or performance at larger queue counts. Network delay,
real route planning, return trips, failures, and cross-region fleet reassignment
are outside the experiment.
