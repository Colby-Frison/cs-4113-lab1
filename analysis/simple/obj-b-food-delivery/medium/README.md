# Medium food-delivery study: 20 queues

The layout follows the matchmaking studies on `obj-b-study-results`:

- **Keep in Git:** study scripts, `study.json`, these notes, and all PNGs under `results/`.
- **Local only:** `runs/` contains raw logs, input/sink CSVs, configurations and run metadata.
  Generated CSV/log/JSON files under `results/` are also ignored by Git.
- `results/<scenario>/` contains the six main plots; its `tools/` folder contains
  the Objective A accessory plots and local intermediate outputs.

Raw evidence is preserved on this computer. Links to CSVs, logs, run archives,
and audit JSON below work locally; a fresh clone must collect runs before using
`--report` or the saved-data audit. PNGs and analysis notes remain available in Git.

Completed **7 sets × 6 main plots = 42 plots**, using the C++/gRPC application.
The network has **15 restaurant kitchen queues + 5 regional pickup queues**.
Each scenario has three 10,000-order runs: **21 runs and 210,000 orders** overall.
Seed 1 is the preselected representative for the six plots in each set; the
comparison below averages all three seeds.

Start with the [comparison figure](results/sensitivity-comparison.png), then open a
scenario gallery below. [METHODS.md](METHODS.md) explains the architecture,
queue mapping, workload distributions, capacity changes, metrics, and commands.

## Measured results

The sensitivity parameter is **total courier fleet size**, with 30 kitchen
workers fixed. Changes are not percentages of all workers. Regional allocations
for reductions are specified in the table; integer fleets prevent equal per-region
percentage reductions.

| Set / plot gallery | Regional couriers | Delivered orders/s | Mean latency (s) | Mean run P95 (s) | Loss (%) | Measured offered orders/s |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| [baseline](results/baseline/README.md) | 4 / 4 / 4 / 4 / 4 | 321.85 | 0.714 | 1.601 | 20.29 | 428.39 |
| [couriers-plus25](results/couriers-plus25/README.md) | 5 / 5 / 5 / 5 / 5 | 299.56 | 0.445 | 1.240 | 6.37 | 336.54 |
| [couriers-plus50](results/couriers-plus50/README.md) | 6 / 6 / 6 / 6 / 6 | 396.63 | 0.315 | 0.984 | 4.52 | 430.00 |
| [couriers-plus100](results/couriers-plus100/README.md) | 8 / 8 / 8 / 8 / 8 | 348.71 | 0.226 | 0.729 | 2.50 | 368.18 |
| [couriers-minus10](results/couriers-minus10/README.md) | 4 / 4 / 4 / 3 / 3 | 319.68 | 0.904 | 1.596 | 22.02 | 434.05 |
| [couriers-minus20](results/couriers-minus20/README.md) | 4 / 3 / 3 / 3 / 3 | 283.66 | 1.026 | 1.779 | 31.89 | 448.46 |
| [couriers-minus30](results/couriers-minus30/README.md) | 3 / 3 / 3 / 3 / 2 | 259.39 | 1.259 | 1.889 | 37.05 | 445.89 |

Latency describes delivered orders; rejected orders are included in loss, not
latency. Throughput includes the complete run and drain interval. Three-seed
ranges appear in [metrics-by-scenario.csv](results/metrics-by-scenario.csv) and the
comparison figure; they are not confidence intervals.

![Sensitivity comparison](results/sensitivity-comparison.png)

## What the measurements show

**The baseline has both regional and restaurant bottlenecks.** Across seeds,
q15, q16, and q17 have mean utilization of approximately 95%, 94%, and 92%.
Their mean waiting populations are 58.4, 56.7, and 38.9 orders. The quieter
region-4 pickup queue q19 averages 52% utilization and 0.26 waiting orders.
The baseline therefore cannot use spare capacity in one region to clear another
region's backlog. Restaurants r0 and r3 also have long kitchen queues: mean
waiting populations 26.9 and 24.4, with about 309 and 155 rejections per run.
See [queue metrics](results/metrics-by-queue.csv) and [regional metrics](results/metrics-by-region.csv).

**Additional couriers relieve pickup congestion but do not fix kitchen capacity.**
With 30 couriers (+50%), mean delivered latency was 0.315 s versus 0.714 s at
baseline, and loss was 4.52% versus 20.29%. That scenario averaged 428 kitchen
rejections and 23 pickup rejections per run. With 40 couriers (+100%), there
were **zero pickup rejections in all three runs**, but 750 total kitchen
rejections remained. The observed residual bottleneck is at the kitchens.

**Removing couriers increases pickup waiting and losses.** With 14 couriers
(−30%), mean delivered latency rose to 1.259 s and loss to 37.05%; about 3,204
orders per run were rejected at pickup. The −10% allocation removes couriers
only from regions 3 and 4, preserving the fleets in the most heavily loaded
regions. This explains why reductions should be interpreted with their regional
allocations, not just the overall percentage.

**The longest baseline orders spend most of their time waiting.** In seed 1,
`o5270.r0.mid.i4` takes 1.879 s: 0.816 s waiting at restaurant r0, 0.060 s kitchen
service, 0.947 s waiting for a region-0 courier, and 0.056 s delivery service.
About 94% of its latency is waiting. The other two longest orders follow the
same route. The [phase breakdown](results/baseline/longest-job-breakdown.csv) and
[trace plot](results/baseline/longest-jobs.png) support this interpretation.

## Arrival-rate variation limits the sensitivity comparison

These are live wall-clock runs on one host, not an exact replay of arrivals.
The configured mean requested gap is 1.5 ms (666.7 orders/s), but actual sleeps,
logging, and scheduling produce lower and variable measured offered rates.
All same-seed order attributes and requested service times match across scenarios.

In particular, +25% seed 3 offered only **186.95 orders/s**, compared with
403.46 and 419.21 in its other two runs. It contained 50 arrival gaps exceeding
100 ms, including two above one second. +100% seed 3 also had a lower offered
rate (304.44 orders/s versus approximately 400 for seeds 1 and 2).
These observations are retained, not replaced or silently excluded.
[Arrival diagnostics](results/arrival-diagnostics.csv) records maximum gaps and counts;
[per-run metrics](results/metrics-by-run.csv) records the actual offered rate.

Consequently, the lower aggregate throughput for +25%, or lower throughput for
+100% than +50%, **does not demonstrate that extra couriers reduce capacity**.
Reduced offered load also contributes to lower losses and latency. The queue
traces support the bottleneck explanations, but these measurements do not isolate
a pure causal fleet-size effect or establish an optimal fleet. A follow-up would
use a controlled arrival replay with measured injection lag, or a discrete-event
model, before making a capacity recommendation.

## Evidence and validation

- [plot-manifest.csv](results/plot-manifest.csv): all 42 required plot paths.
- [comparison.csv](results/comparison.csv): means and same-seed percentage comparisons.
- [metrics-by-run.csv](results/metrics-by-run.csv): all 21 runs, including workload hashes.
- [metrics-by-queue.csv](results/metrics-by-queue.csv): 420 queue/run records.
- [metrics-by-region.csv](results/metrics-by-region.csv): 105 region/run records.
- Each scenario's `tools/` directory demonstrates all ten shared accessory tools,
  with exact commands, CSVs, filtered logs, pretty traces, and supporting plots.
- Each scenario's `runs/<scenario>/seed-N.tar.gz` preserves the original source, queue/server
  logs, sinks, normalized events, queue metrics/config, and dispatcher snapshots.
  [archive-manifest.csv](results/archive-manifest.csv) records sizes and checksums; archives
  retain byte-identical study evidence. Historical readiness-port files and empty
  diagnostic logs were removed during cleanup.
- [validation.json](results/validation.json) and [audit.json](results/audit.json): **172,609 delivered
  + 37,391 rejected = 210,000**, with zero incomplete orders. Audit checks include
  unique unmodified sinks, region routing, event phases, drained registries, worker
  bounds, time-weighted integration, tool counts, longest-job selection, and PNGs.
- Application regression and network tests: **27 passed**. New tests cover routing
  across shared regions, complete draining, overload accounting, and independent
  restaurant/workload RNGs.
- [provenance.json](runs/provenance.json) records collection source/binary/config hashes
  and host versions.

To inspect raw evidence, extract an archive to a scratch directory. To regenerate
plots, `run_study.py --report` transparently reads directories or archives. To
audit them again, run `validate_study.py` using the application's `.venv` Python.
Collection with `--collect` reuses complete saved runs and rejects changed inputs.
See [METHODS.md](METHODS.md) for the complete commands and limitations.
