# Study runner

Every size is a pattern. [run_study.py](run_study.py) expands that pattern into numeric queues, scales the stages named in the pattern, starts the pipeline, and draws the figures. Small, Medium, Large, and Very Large use that same runner on Linux. Small is short enough to finish locally. Medium and the larger bands use the [remote kit](remote/README.md) on a Linux box (`run_medium.sh`, `run_large.sh`, `run_very_large.sh`) because the single ready worker is past capacity at the tuned arrival. What the Small numbers mean is [small/README.md](small/README.md). The module docstring at the top of `run_study.py` is the map of how one invocation expands, starts, polls, and plots.

The study does not start containers. Containers are the other launcher, [orchestration/README.md](../orchestration/README.md), and they read [topology/baseline.json](../topology/baseline.json) unchanged. A study overrides workers, depth, count, arrival, and time scale in its pattern and leaves that JSON as the product topology.

## How to run

From the repository root:

```text
python implementation/obj-b.matchmaking/studies/run_study.py --size small
python implementation/obj-b.matchmaking/studies/run_study.py --size small --only base
python implementation/obj-b.matchmaking/studies/run_study.py --size medium --only base,srv-30 --count 1000
python implementation/obj-b.matchmaking/studies/run_study.py --size large --only srv- --sims-only
python implementation/obj-b.matchmaking/studies/run_study.py --size large --only srv+ --sims-only
python implementation/obj-b.matchmaking/studies/run_study.py --size large --plots-only
python implementation/obj-b.matchmaking/studies/expand.py very-large
```

`studies/small/run_small.py` forwards to `--size small`.

| Flag | Default | Effect |
| --- | --- | --- |
| `--size` | required | `small`, `medium`, `large`, or `very-large` |
| `--only` | all seven | Comma-separated names and/or groups. Names: `base`, `srv-10`, `srv-20`, `srv-30`, `srv+25`, `srv+50`, `srv+100`. Groups: `srv-` (three cuts), `srv+` (three boosts) |
| `--count` | the pattern | Tickets in that scenario. The buffer depth follows this count |
| `--time-scale` | the pattern | Multiplies every drawn wait and the interarrival |
| `--arrival-mean` | tuned | Unscaled seconds between tickets. Omit it and the runner sets one baseline search queue near $\rho = 0.8$ |
| `--runs` | `studies/<size>/runs` | Process logs and sinks |
| `--results` | `studies/<size>/results` | Figures and `summary.md` |
| `--plots-only` | off | Skip the processes and redraw figures from logs already in `--runs` |
| `--sims-only` | off | Run the pipeline until tickets are accounted; skip analysis, figures, and `summary.md` |

The terminal that launched the script is the live count. It prints the size, then one line per scenario, then a sink line about every 5 seconds, then one summary line when that set finishes:

```text
small: 6 queues, backend process, tickets 1000, arrival 1.7181s
scenario base backend process search workers 10 allocate workers 10
  sinks matched=552 failed=18 queue-full=0
  matched 957 failed 43 queue-full 0 latency 15159.4 ms
summary: ...\studies\small\results\summary.md
```

`matched` and `failed` are data rows in the two sink CSVs. `queue-full` is distinct job ids in the process logs that contain `ERROR QUEUE-FULL`. The runner stops the processes when those three counts add up to `--count`. If the counts sit still for 45 seconds, or the deadline passes, it kills the processes and raises.

A second terminal can follow one process while the set is still running. From the repository root on Linux:

```text
tail -f implementation/obj-b.matchmaking/studies/small/runs/base/q3.log
tail -f implementation/obj-b.matchmaking/studies/small/runs/base/matched.csv
```

`q3.log` is the search queue on Small, and the first search shard on the later sizes. `sf-q4.log` is the Small ready server, which is where a retry and a ready-check failure show up. Large and Very Large fold every queue into `q-host.log` and `sf-host.log`. The PNG files appear only after that scenario's processes have been killed.

## The pattern

A person edits `studies/<size>/pattern.json`. They do not edit a thousand queue entries. [expand.py](expand.py) reads the file and stamps the queues.

| Field | Role |
| --- | --- |
| `shared_stages` | `admit`, `rating`, `route`, `ready`, `allocate`, in that order |
| `replicated_stage` | `search`. This is the only stage the expander copies |
| `axes` | `region`, `mode`, `band`. The ticket id already carries these |
| `regions`, `modes` | Optional subset. Omit them and the expander uses the full tables. One entry pins the generator to that token |
| `search_grouping` | `bracket` copies search once per region, mode, band, and replica. `single` keeps one search queue for every ticket |
| `replicas` | Shard count inside a bracket. `seq % replicas` sends a ready-check retry back to the same queue. A single search queue uses `1` |
| `swept_stages` | Stages whose worker count follows the seven factors. Defaults to `search` |
| `baseline_search_workers` | Workers on each swept stage before a factor is applied |
| `shared_workers` | Workers on every stage that is not swept |
| `qsize`, `count` | Buffer depth and tickets. A `--count` override sets the buffer to that same count |
| `time_scale`, `ready_fail_p`, `max_ready_visits`, `target_rho`, `seed` | Workload and the arrival target |

Small is `search_grouping: single`, regions `na`, modes `ranked`, and `swept_stages` of `search` and `allocate`, with a baseline of 10. Skill band still changes the search wait. It does not add a queue, so the study stays inside the 2–10 queue band. Medium, Large, and Very Large leave the axes unset, group by bracket, and sweep search only.

## What one scenario starts

Eighty queues is the cut. Small (6) and Medium (45) stay under it: one `queue.py` and one `server_func.py` per queue. Large and Very Large use [host/queue_host.py](host/queue_host.py) and [host/server_host.py](host/server_host.py).

### One process pair per queue

`spawn` runs `subprocess.Popen` on two programs for each logical queue:

| Process | Program | Log file | Stderr |
| --- | --- | --- | --- |
| Queue `qN` | `implementation/obj-a/queue/queue.py` | `runs/<scenario>/qN.log` | `qN.err` |
| Server for that queue | `implementation/obj-b.matchmaking/server-stages/server_func.py` | `runs/<scenario>/sf-qN.log` | `sf-qN.err` |

Stdout of those processes is discarded. The Lab 0 line is written by `--logfile`. The prefix is `Q_pid…` on a queue and `sv_pid…` on a server. The worker count is the gRPC thread pool inside the one server process, passed as `127.0.0.1:PORT,N`.

Startup order is every server, then every queue except admit. The runner waits until each of those logs contains `gRPC listen`, then starts admit, then the generator. Listeners are up before any ticket is sent. Each scenario takes a block of host ports at `29100 + scenario_index * 200`, so a leftover process from the previous set does not collide, and these ports stay clear of the orchestrator's container ports.

### One hosted pair

Above 80 queues, two processes hold the whole graph. The queue host has one buffer per numeric qid and one listen port. The server host sleeps the stage wait, then submits the row back with `dest_qid` set to the next logical queue. An empty `dest_qid` is the single-queue path, which ignores the field. Each logical queue has a concurrency limit equal to its server count, so utilization still divides by `Num. Servers`. The file-only path does not import grpc.

## How a ticket is routed

The processes never open `baseline.json`. The expander writes `bracket.json`, and the route and ready servers load it. The generator sleeps the interarrival, then writes one CSV row to `jobs.csv` and to a POSIX FIFO under a native temp directory (so a Windows-mounted repo path still works from WSL). Admit (`q0`) is the only reader of that FIFO. A regular file would look finished as soon as the reader caught the writer, which dumps every remaining ticket at once and breaks the ready-check loop. The FIFO is only the first hop. Every later hop is gRPC on `127.0.0.1`.

```text
generator --pipe--> q0 --grpc--> validate --grpc--> q1 --grpc--> rating
        --grpc--> q2 --grpc--> route --grpc--> search shard
        --grpc--> ready --grpc--> allocate --> matched.csv
                       \--grpc--> same shard   (retry)
                       \--> failed.csv         (visit cap)
```

Logged ids are numeric because Objective A `compute-use` casts the queue id to an integer. `q0` is admit, `q1` rating, `q2` route, then a contiguous search range, then ready, then allocate.

On a bracket layout the search offset is

$$
((\text{region} \times \text{modes} + \text{mode}) \times \text{bands} + \text{band}) \times \text{replicas} + (\text{seq} \bmod \text{replicas})
$$

and the queue id is `q` plus `3` plus that offset. Route and the ready-check retry both use it, so a retry returns to the same shard. A single-search layout skips the formula and sends every ticket to `q3`.

Ready's other edges are `--pass` (the allocate listen) and `--fail` (`failed.csv`). Allocate's only output is `--outfile matched.csv`. The server sleeps the one wait that belongs to its stage and forwards the original row.

When the pattern names one region or one mode, the generator pins that token. Small therefore emits `na` and `ranked` across every skill band. A pattern that leaves the axis off the file spreads tickets across the full table, and an empty bracket is a traffic outcome.

## Server sweep

[scale.py](scale.py) copies the total-preserving scale from the Objective A sanity check. The seven factors are $1$, $0.9$, $0.8$, $0.7$, $1.25$, $1.5$, and $2$. Each swept stage is floored, kept at least at 1, then nudged until its total is the rounded factor times its baseline. Leftover workers are handed out one queue at a time, so $-10\%$, $-20\%$, and $-30\%$ stay different totals when a queue's own floor would have tied them.

Small sweeps search and allocate together from a baseline of 10, which is the integer series $10$, $9$, $8$, $7$, $12$, $15$, $20$. Admit, rating, route, and ready stay at 1. Medium and the larger sizes sweep search only, from 4 workers on every search queue, and leave the shared stages at 1. The same arrival that loads search saturates the single ready worker. `summary.md` records that.

## Arrival

The unscaled interarrival that puts one baseline search queue near the target load is

$$
\frac{E[S]\, E[V]}{\rho \, c \, N}
$$

$E[S]$ is the mean search wait after the band cuts and the party mix. $E[V]$ is the expected search visits, $1 + 0.2 = 1.20$ at the default failure probability and visit cap. $c$ is the baseline workers on one search queue. $N$ is the number of search queues. Small has $N = 1$ and $c = 10$, so the mean is $1.718$ s and the single ready worker sits near $\rho = 1.40$. A later size divides by its shard count, so one baseline shard sits near $\rho = 0.8$ while the ready worker is further past capacity. Region weights are uneven, so some shards run hotter. The chosen interarrival is written into `summary.md`. The same arrival is reused across the seven factors.

## What is written

| Path | What it is |
| --- | --- |
| `runs/<scenario>/jobs.csv` | Every ticket the generator wrote |
| `runs/<scenario>/matched.csv` | Passed ready and allocate |
| `runs/<scenario>/failed.csv` | Failed the ready check at the visit cap |
| `runs/<scenario>/qN.log`, `sf-qN.log` | Lab 0 events for a process-pair scenario |
| `runs/<scenario>/q-host.log`, `sf-host.log` | Lab 0 events for a hosted scenario |
| `runs/<scenario>/*.err` | Stderr, including a traceback if a process died |
| `runs/<scenario>/servers.csv` | `qid`, buffer size, worker count. The analyzer reads this as `--qconf-file` |
| `runs/<scenario>/bracket.json` | Search edges for route and ready-retry |
| `runs/<scenario>/ready-state.txt` | Ready-check visit count per job id |
| `runs/<scenario>/analysis/` | Tables from `analyze_run.py` |
| `results/<scenario>/*.png` | The six figures |
| `results/<scenario>/tools/` | Accessory figures and the filtered search `qmet` |
| `results/summary.md` | One row per scenario |
| `results/sensitivity-servers.png`, `sensitivity-latency.png` | The seven search totals, ordered from smallest to largest |

Each size's `.gitignore` ignores `runs/` and the result logs and CSVs. Commit the runner, the patterns, the READMEs, the PNGs, and `summary.md`.

After the sinks fill, `run_scenario` calls `implementation/obj-a/mockups/analyze_run.py`, then `filter-by-queue` for the first search queue. `write_six_plots` copies utilization, saturation, errors, latency, throughput, and the three longest jobs into `results/<scenario>/`. Past Small, the USE panels draw one line per stage and highlight the busiest search queue. Latency, throughput, and the three longest jobs stay global. `errors.png` is `QUEUE-FULL` only. Ready-check failures stay in their own column of `summary.md` and their own series on `sensitivity-servers.png`.

## Where the code lives

| Piece | Module | Role |
| --- | --- | --- |
| Pattern | `studies/<size>/pattern.json` | The file a person edits |
| Load and stamp | `expand.py` | Numeric queues, `servers.csv`, `bracket.json`, the host spec |
| Pools | `scale.py` | The seven factors, total-preserving |
| Arrival | `arrival.py` | $E[S]$, $E[V]$, and the interarrival |
| Admit edge | `job_pipe.py` | POSIX FIFO plus a copy of the same bytes in `jobs.csv` |
| Remote kit | `remote/` | Shell scripts and notes for Medium, Large, and Very Large on Linux boxes |
| The run | `run_study.py` | Backend choice, listeners, generator, sink polling, analysis |
| Hosted pair | `host/queue_host.py`, `host/server_host.py` | Many logical queues in two processes |
| Routing | `mm/stages.py` | Bracket formula, the single-queue shortcut, and the region map used when no bracket is loaded |
| Figures | `plots.py` | Six PNGs, `summary.md`, and the two sensitivity charts |

## Sizes

| Size | Search queues | Queues | Tickets | Backend | Pool under test |
| --- | --- | --- | --- | --- | --- |
| Small | 1 | $5 + 1 = 6$ | 1,000 | one process pair per queue | search and allocate, baseline 10 |
| Medium | 40 | $5 + 40 = 45$ | 10,000 | one process pair per queue | search, baseline 4 per shard |
| Large | 200 | $5 + 200 = 205$ | 100,000 | one queue host and one server host | search, baseline 4 per shard |
| Very Large | 1,000 | $5 + 1,000 = 1,005$ | 100,000 | one queue host and one server host | search, baseline 4 per shard |

Medium is $4$ regions $\times$ $2$ modes $\times$ $5$ skill bands at one replica. Large uses 5 replicas. Very Large uses 25. `baseline.json` stays the eight-ticket product topology.
