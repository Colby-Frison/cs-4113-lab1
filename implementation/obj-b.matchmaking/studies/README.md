# Study runner

The Small study is the worked example of a size run. Copy this shape for Medium and Large. What the Small numbers mean is [small/README.md](small/README.md). This file is how `studies/small/run_small.py` starts the pipeline, where a ticket goes, and what to keep when the next size is written.

The study does not start containers. Twelve local Python processes run one scenario, then the runner stops them and draws the figures. Containers are the other launcher, [orchestration/README.md](../orchestration/README.md), and they read [topology/baseline.json](../topology/baseline.json) unchanged. A study overrides workers, depth, count, arrival, and time scale in its own script and leaves that JSON as the product topology.

## How to run

From the repository root:

```text
python implementation/obj-b.matchmaking/studies/small/run_small.py
python implementation/obj-b.matchmaking/studies/small/run_small.py --only base
python implementation/obj-b.matchmaking/studies/small/run_small.py --only base,srv-30 --count 1000
python implementation/obj-b.matchmaking/studies/small/run_small.py --plots-only
```

| Flag | Default | Effect |
| --- | --- | --- |
| `--only` | all seven | Comma-separated scenario names: `base`, `srv-10`, `srv-20`, `srv-30`, `srv+25`, `srv+50`, `srv+100` |
| `--count` | 1000 | Tickets in that scenario |
| `--time-scale` | 0.02 | Multiplies every drawn wait and the interarrival |
| `--arrival-mean` | tuned | Unscaled seconds between tickets. Omit it and the runner sets the baseline pool of 10 near $\rho = 0.8$ |
| `--runs` | `studies/small/runs` | Process logs and sinks |
| `--results` | `studies/small/results` | Figures and `summary.md` |
| `--plots-only` | off | Skip the processes and redraw figures from logs already in `--runs` |

The terminal that launched the script is the live count. It prints one line per scenario, then a sink line about every 5 seconds, then one summary line when that set finishes:

```text
scenario base search/allocate workers 10
  sinks matched=552 failed=18 queue-full=0
  matched 957 failed 43 queue-full 0 latency 15159.4 ms search util 0.980 (86s)
summary: ...\studies\small\results\summary.md
```

`matched` and `failed` are data rows in the two sink CSVs. `queue-full` is distinct job ids in the process logs that contain `ERROR QUEUE-FULL`. The runner stops the twelve processes when those three counts add up to `--count`. If the counts sit still for 45 seconds, or the deadline passes, it kills the processes and raises.

A second terminal can follow one process while the set is still running. PowerShell, from the repository root:

```text
Get-Content -Wait implementation\obj-b.matchmaking\studies\small\runs\base\q3.log
Get-Content -Wait implementation\obj-b.matchmaking\studies\small\runs\base\matched.csv
```

`q3.log` is the search queue. `sf-q4.log` is the ready server, which is where a retry and a ready-check failure show up. The PNG files appear only after that scenario's processes have been killed.

## What one scenario starts

`spawn` runs `subprocess.Popen` on two programs:

| Process | Program | Log file | Stderr |
| --- | --- | --- | --- |
| Queue `q0` … `q5` | `implementation/obj-a/queue/queue.py` | `runs/<scenario>/qN.log` | `qN.err` |
| Server for that queue | `implementation/obj-b.matchmaking/server-stages/server_func.py` | `runs/<scenario>/sf-qN.log` | `sf-qN.err` |

Stdout of those processes is discarded. The Lab 0 line is written by `--logfile`. With no `--logfile` the same line would go to stderr only. The prefix is `Q_pid…` on a queue and `sv_pid…` on a server.

The worker count is the gRPC thread pool inside the one server process, passed as `127.0.0.1:PORT,N`. Search and allocate use the scenario pool (10, 9, 8, 7, 12, 15, 20). Admit, rating, route, and ready stay at 1, because a pool of 1 has nothing to shrink by 10%.

Startup order is the servers first, then queues `q1` through `q5`. The runner waits until each of those logs contains `gRPC listen`, then starts `q0`, then the generator. Listeners are up before any ticket is sent. Each scenario takes a block of host ports at `28100 + scenario_index * 20`, so a leftover process from the previous set does not collide, and these ports stay clear of the orchestrator's container ports.

## How a ticket is routed

The processes never open `baseline.json`. `_queue_args` and `_server_args` are the wiring. The generator sleeps the interarrival, then writes one CSV row to `jobs.csv` and to a Windows named pipe `\\.\pipe\mm-small-<scenario>-<pid>`. `q0` is the only reader of that pipe. A regular file would look finished as soon as the reader caught the writer, which dumps every remaining ticket at once and breaks the ready-check loop. The pipe is only the first hop. Every later hop is gRPC on `127.0.0.1`.

```text
generator --pipe--> q0 --grpc--> validate --grpc--> q1 --grpc--> rating
        --grpc--> q2 --grpc--> route --grpc--> q3 --grpc--> search
        --grpc--> q4 --grpc--> ready --grpc--> q5 --grpc--> allocate --> matched.csv
                                    \--grpc--> q3          (retry)
                                    \--> failed.csv       (second failure)
```

Route's flag is `--route-edge na=127.0.0.1:<q3 port>,1`. Ready's flags are `--pass` (dial `q5`), `--retry` (dial `q3`), and `--fail` (the path `failed.csv`). Allocate's only output is `--outfile matched.csv`. The server sleeps the one wait that belongs to its stage and forwards the original row.

Logged ids are `q0` through `q5` because Objective A `compute-use` casts the queue id to an integer. `q-admit` would throw. The stage names live in the Small README.

| Log id | Stage | Where the row goes next |
| --- | --- | --- |
| `q0` | admit / validate | `q1` |
| `q1` | rating | `q2` |
| `q2` | route | `q3` for region `na` |
| `q3` | search | `q4` |
| `q4` | ready | `q5`, or back to `q3`, or `failed.csv` |
| `q5` | allocate | `matched.csv` |

## What is written

| Path | What it is |
| --- | --- |
| `runs/<scenario>/jobs.csv` | Every ticket the generator wrote |
| `runs/<scenario>/matched.csv` | Passed ready and allocate |
| `runs/<scenario>/failed.csv` | Failed the ready check at the visit cap |
| `runs/<scenario>/qN.log`, `sf-qN.log` | Lab 0 events for that process |
| `runs/<scenario>/*.err` | Stderr, including a traceback if a process died |
| `runs/<scenario>/servers.csv` | `qid`, buffer size, worker count. The analyzer turns this into `qconf.csv` |
| `runs/<scenario>/ready-state.txt` | Ready-check visit count per job id |
| `runs/<scenario>/analysis/` | Tables from `analyze_run.py` |
| `results/<scenario>/*.png` | The six figures |
| `results/<scenario>/tools/` | Accessory figures and `q3-qmet.csv` |
| `results/summary.md` | One row per scenario |

`studies/small/.gitignore` ignores `runs/` and the result logs and CSVs. Commit the runner, the READMEs, the PNGs, and `summary.md`.

After the sinks fill, `run_scenario` calls `implementation/obj-a/mockups/analyze_run.py` with `--qconf-file` pointing at `servers.csv`, then `filter-by-queue` for `q3`. `write_six_plots` copies utilization, saturation, errors, latency, throughput, and the three longest jobs into `results/<scenario>/`. `errors.png` is `QUEUE-FULL` only. Ready-check failures stay in their own column of `summary.md`.

## Where to look in `run_small.py`

The file is long because it is the supervisor and the plotting step in one script. The queue and the stage server stay in Objective A and `server-stages/`.

| Piece | Functions | Role |
| --- | --- | --- |
| Load | `expected_search_mean`, `tuned_arrival_mean`, `SCENARIOS` | Seven pools and the arrival that makes the baseline pool busy |
| Admit edge | `JobPipe`, `_Tee` | Named pipe plus a copy of the same bytes in `jobs.csv` |
| Processes | `spawn`, `stop`, `wait_for_listen` | Start, kill, and wait for `gRPC listen` |
| Wiring | `_assign_ports`, `_queue_args`, `_server_args` | One command line per process |
| The run | `execute_pipeline` | Start listeners, start the generator, poll the sinks |
| One scenario | `run_scenario` | Clear the directory, run, call the Objective A tools |
| Figures | `publish_tools`, `write_six_plots`, `write_summary` | PNGs and the comparison table |
| Entry | `main` | Flags, then the scenarios in order |

## What the next size copies

- One directory per scenario under `studies/<size>/runs/<scenario>/` and `studies/<size>/results/<scenario>/`.
- Numeric queue ids, and a `servers.csv` with columns `qid`, `qsize`, `servers`.
- Listeners before the generator. gRPC after the first hop on Windows, so a ready-check retry still has a live peer.
- One gRPC channel per peer. That reuse lives in `implementation/obj-a/lib/grpc_runtime.py`. A new TCP connection per ticket exhausts Windows ephemeral ports before the second scenario finishes.
- `QUEUE-FULL` and an application failure in separate columns. `errors.png` counts only `QUEUE-FULL`.
- Overrides in the runner. Leave `topology/baseline.json` as the product description the orchestrator reads.
- A baseline pool large enough that `floor` of $-10\%$, $-20\%$, and $-30\%$ are different integers. A baseline of 4 makes $-10\%$ and $-20\%$ the same run.
- An arrival retuned for that baseline pool. The product interarrival of 14 s is for one search server. A pool of 10 at that arrival sits idle, and adding workers does not move latency.
- Gitignore `runs/` and result logs and CSVs. Commit the PNGs, `summary.md`, and the README.

Growing the queue count for Medium and Large is a generated pattern, not a longer hand-written pipeline. Four regions, two modes, and five skill bands are $4 \times 2 \times 5 = 40$ search queues plus the shared stages. A replica count carries that into the larger bands. The 1,000–10,000 band runs the generated `qconf.csv` with many queues inside one process. One container, or one process, per queue does not scale to that band. The Objective A [topology section](../../obj-a/README.md#topology) is the description the other group members already have for that step.
