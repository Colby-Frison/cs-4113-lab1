# Medium wait-job sanity check

Objective A's medium-band sanity check: prove the shared wait-job framework still admits, serves, logs, and plots when the queue count sits at the top of the tasksheet's 10–100 band.

This is **not** a domain model. Each of the 100 systems is the same Lab 1 sanity shape — a CSV of wait tasks, one queue process, one stateless server function, one sink — running side by side with no edges between them. Packet networks, ticket offices, matchmaking, and other applications belong under someone's Objective B.

## What this study asks

The lab slide for MEDIUM asks for on the order of 100 queues and 10,000–100,000 wait jobs, then the same questions as a small study: mapping, metrics, configuration, workload, and how ± servers change throughput and latency. This directory is that run.

Defaults used here:

| Knob | Value | Why |
| --- | --- | --- |
| Queues | 100 | Top of the 10–100 band |
| Jobs | 10,000 (100 per queue) | Low end of 10,000–100,000 |
| Wait per job | 0.05 s | Sleep dominates service time; a scenario still finishes in about a minute |
| Arrival | Fixed 0 (burst) | Stress servers, not idle time under a slow Poisson arrival |
| Baseline servers | 4 per queue (400 total) | −10% / −20% / −30% can actually shrink; a baseline of 1 cannot |
| Queue depth | 100 (= burst size) | Every job is admitted; servers are the resource under test |

Live process logs stay in `runs/` (gitignored). Checked-in notes and plots live under `results/`. Regenerate everything with:

```text
python implementation/obj-a/sanity-check/medium/run_medium.py
```

Optional filters: `--only base,srv-30`, `--queues`, `--jobs-per-queue`, `--wait`. The script itself documents each stage in its module and function docstrings.

## How `run_medium.py` works

One invocation walks the seven server-factor scenarios (or a `--only` subset). For each scenario it:

1. Clears `runs/<scenario>/` and scales the 4-server baseline by that scenario's factor (`floor`, at least 1, then nudge the total).
2. Generates one seeded wait-job CSV and splits it round-robin into `jobs-q0.csv` … `jobs-q99.csv` so ids stay unique (`j0` … `j9999`).
3. Starts every server function first, waits until each log shows `gRPC listen`, then starts the matching queue processes.
4. Polls the sinks until all jobs are delivered, stops the children, and runs `analyze_run.py`.
5. Copies tool artifacts into `results/<scenario>/tools/` and draws the six rubric plots.

After the last scenario it writes `results/summary.md` and `results/sensitivity-latency.png`. See the module docstring in [`run_medium.py`](run_medium.py) for the full ordered map.

## 1. What the system is

A job is one wait. The server function sleeps for the task and writes the job to a sink. Nothing about the job is interpreted. One hundred copies of that system run side by side so the queue count is the top of the tasksheet's 10–100 band.

There is no topology between the queues.

## 2. How it is mapped

| Real thing in the sanity check | Queue system |
| --- | --- |
| Wait job (`j…` with one `W 0.050` task) | Job |
| One buffer plus its server function | One queue, `q0` … `q99` |
| Buffer | `qsize` 100, equal to the burst |
| Servers | gRPC worker count on that queue's server function |
| Source | `jobs-q<id>.csv`, produced by the Obj A workload generator |
| Finished job | One row in `sink-q<id>.csv` |
| Rejected job | `ERROR QUEUE-FULL` |

Startup order is the framework's order. Every server function binds its port first. The queues start after `gRPC listen` is in those logs, then read their files and submit jobs. A queue process exits when its file is drained. The server functions are stopped after every sink has one row per job.

## 3. Workload

The generator is `implementation/obj-a/workload-generator`. One seeded stream writes 10,000 jobs:

```text
--arrival-dist fixed --arrival-fixed 0
--wait-dist fixed --wait-fixed 0.05
--tasks 1 --count 10000 --seed 1
```

Arrival delay 0 makes a burst: the interesting question at this scale is what happens when 100 jobs are already waiting on each queue. A slow Poisson arrival would leave the servers idle, and adding servers would not move latency. The wait is 0.05 s so the sleep, not the RPC, is most of the service time, and a scenario still finishes in about a minute.

The stream is split round-robin into `jobs-q0.csv` … `jobs-q99.csv`. Job ids stay unique across queues (`j0` … `j9999`), 100 jobs each. 10,000 jobs is the low end of the slide's 10,000–100,000 band.

## 4. The seven sets

Baseline is 4 servers on every queue, 400 servers in total. One server per queue cannot shrink under the "at least one" rule, so a baseline of 1 would make −10% / −20% / −30% a no-op. Queue depth stays 100 in every set, so a drop would mean the buffer could not hold the burst. It never happens here. The resource that changes is the server count: `floor(4 × factor)`, at least 1, with the total nudged to `factor` times 400.

| Set | Factor | Servers | Per queue |
| --- | --- | --- | --- |
| `base` | 1.00 | 400 | 4 |
| `srv-10` | 0.90 | 360 | 3–4 |
| `srv-20` | 0.80 | 320 | 3–4 |
| `srv-30` | 0.70 | 280 | 2–3 |
| `srv+25` | 1.25 | 500 | 5 |
| `srv+50` | 1.50 | 600 | 6 |
| `srv+100` | 2.00 | 800 | 8 |

## 5. How to read the metrics

Utilization is jobs in service divided by that queue's server count. Saturation is jobs waiting in the buffer. Errors are `QUEUE-FULL` drops. Latency is file arrival to sink departure. Throughput is jobs that reach a sink in that millisecond.

For a burst of $N = 100$ jobs, $S$ servers, and service $W = 50$ ms, job $k$ finishes at about $\lceil k/S \rceil W$. The mean is about $W(N/S + 1)/2$. Baseline $S = 4$ predicts about 650 ms. The run measured 714 ms. The gap is the gRPC handoff on top of the sleep.

## 6. What the runs did

Every set delivered all 10,000 jobs and logged no `QUEUE-FULL`. Peak utilization was 1.0 in every set: during the burst the servers were full. Peak saturation was about 90–98, so the buffer held almost the whole burst before the drain. The number that moves is latency.

| scenario | servers | mean latency (ms) |
| --- | --- | --- |
| `srv-30` | 280 | 1193.4 |
| `srv-20` | 320 | 902.5 |
| `srv-10` | 360 | 863.9 |
| `base` | 400 | 713.8 |
| `srv+25` | 500 | 535.4 |
| `srv+50` | 600 | 522.5 |
| `srv+100` | 800 | 394.8 |

The full table, including peak utilization and saturation, is [results/summary.md](results/summary.md). `results/sensitivity-latency.png` is the same latency series.

Cutting servers from 4 to 2–3 raised mean latency from 714 ms to 1193 ms. Adding servers to 5 cut it to 535 ms. Going from 5 to 6 barely moved it (535 ms to 522 ms). Doubling the baseline, 4 to 8, cut it to 395 ms. Throughput is the other side of the same drain: more servers empty the burst sooner, so the completion spike is earlier and narrower.

That is the takeaway for someone running this framework. While a burst is sitting in the buffer, response time scales with jobs per server. Extra servers help until the burst is short enough that handoff time is a large part of the wait. They do not change loss when the buffer was already sized to the burst. A smaller `qsize` would turn the same arrival into `QUEUE-FULL` instead of queueing delay.

## 7. Plots and accessory tools

Each `results/<scenario>/` has the six rubric plots:

- `utilization.png`, `saturation.png`, `errors.png`
- `latency.png`, `throughput.png`
- `longest-jobs.png` for the three longest jobs

`q0` is drawn darker. The other 99 queues are the lighter lines. Time on the axes is milliseconds from the first event of that run.

`analyze_run.py` runs the shared tools on the merged log. The figures below are checked in under `results/<scenario>/tools/`. The logs and CSVs are written beside them on a re-run and are gitignored.

| Tool | File |
| --- | --- |
| Merged event log | `tools/events.log` |
| `qmet.csv` and config | `tools/qmet.csv`, `tools/qconf.csv` |
| `filter-by-queue` for `q0` | `tools/q0-qmet.csv` |
| `filter-by-job` for one long job | `tools/longest-job.log` |
| `compute-use` | `tools/quse.csv` |
| `plot-use` | `tools/use.png` |
| `derive-event-metrics` | `tools/emetrics.csv` |
| `derive-sys-metrics` | `tools/sysmetrics.csv` |
| `plot-sys-metrics` | `tools/resplot.png` |
| `plot-derive-event-metrics` | `tools/response-hist.png`, `tools/response-scatter.png` |
| `create-event-trace` | `tools/etrace.png` (first 40 jobs) |
| `pretty-print-job-trace` | `tools/pp-events.log`, `tools/pp-longest.log` |

`qconf.csv` carries the server count for that set, so utilization is in-service divided by 4, 5, 8, and so on, not by a hardcoded 1.

## 8. Limits

- The hundred queues do not talk to each other. This checks that the framework still admits, serves, logs, and plots at medium width. It does not check a network of edges.
- `qmet.csv` records a queue when its waiting or in-service count changes, plus a zero row at step 0. Repeating all 100 queues at every event would multiply the file by 100 and not change the count.
- `etrace.png` is the first 40 jobs so the lifelines stay readable. The long-job requirement is `longest-jobs.png`.
- Process logs in `runs/`, and the result logs and CSVs, are gitignored. Re-running the script rewrites them.
