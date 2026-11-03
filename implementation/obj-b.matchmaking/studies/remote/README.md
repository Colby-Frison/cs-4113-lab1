# Remote study runs

Small finished locally. Medium, Large, and Very Large are long enough that a Linux
box (or a `tmux` session on one) is the practical place to finish them. Every
script here calls the shared runner [run_study.py](../run_study.py). Patterns,
figures, and the seven server factors are unchanged.

Scripts discover the repo root from their own path via [_repo_root.sh](_repo_root.sh)
(`remote/` → four parents up). There are no machine-specific absolute paths.
After sourcing it, `ROOT` is the lab repo and `STUDIES_ROOT` is
`implementation/obj-b.matchmaking/studies`. Commands below still assume you
invoke the script from a clone of that repo; `cd` into `ROOT` happens inside.

| Size | Queues | Backend | Pattern tickets | Why it is slow |
| --- | ---: | --- | ---: | --- |
| Medium | 45 | one process pair per queue | 10,000 | Ready is past capacity (~$\rho \approx 22$ at the tuned arrival) |
| Large | 205 | hosted pair | 100,000 | Same ready bottleneck, more tickets and shards |
| Very Large | 1,005 | hosted pair | 100,000 | Same story at 1,000 search shards |

## Setup

1. Clone or copy the lab repository onto the Linux host.
2. Install Python 3.11+ and deps from the repository root:

```text
python3 -m pip install -r implementation/obj-a/requirements.txt
python3 -m pip install matplotlib pandas
```

3. Free host ports from 29100 upward (each scenario takes a block of 200).
4. Prefer `tmux` or `screen` so a dropped SSH session does not kill the sweep.

The admit edge is a POSIX FIFO under a native temp directory. A Windows-mounted
repo path (WSL `/mnt/c/...`) is fine for code and results; `mkfifo` itself does
not need to succeed on that mount.

## Run

From the repository root:

```text
bash implementation/obj-b.matchmaking/studies/remote/run_medium.sh
bash implementation/obj-b.matchmaking/studies/remote/run_large.sh
bash implementation/obj-b.matchmaking/studies/remote/run_very_large.sh
```

Pass the same flags `run_study.py` accepts after the script name:

```text
bash implementation/obj-b.matchmaking/studies/remote/run_medium.sh --only base
bash implementation/obj-b.matchmaking/studies/remote/run_medium.sh --only base,srv-30
bash implementation/obj-b.matchmaking/studies/remote/run_large.sh --only base
```

Stdout and stderr append to `studies/<size>/remote-run.log`. Live sinks:

```text
tail -f implementation/obj-b.matchmaking/studies/medium/runs/base/matched.csv
tail -f implementation/obj-b.matchmaking/studies/medium/remote-run.log
```

For Medium, start with `--only base` if you want one scenario’s figures before
committing to all seven. Pattern defaults keep `time_scale` 0.02. The arrival
that puts one search shard near $\rho = 0.8$ leaves ready far past capacity, so
plan hours (Medium) to overnight (Large / Very Large) for a full sweep.

`run_local_linux.sh` is a thin wrapper when you already have a Linux shell on
the machine that holds the repo:

```text
bash implementation/obj-b.matchmaking/studies/remote/run_local_linux.sh medium --only base
```

## Pack results

After a size finishes and `summary.md` plus the sensitivity PNGs exist:

```text
bash implementation/obj-b.matchmaking/studies/remote/pack_results.sh medium
bash implementation/obj-b.matchmaking/studies/remote/pack_results.sh large
bash implementation/obj-b.matchmaking/studies/remote/pack_results.sh very-large
```

That writes `studies/<size>/<size>-results.tar.gz` with every `results/**/*.png`
and `results/summary.md`. Copy the archive off the box and unpack it into the
matching `studies/<size>/` tree on the review branch. Do not commit `runs/` or
result logs and CSVs.

## What success looks like

For each scenario, `matched + failed + QUEUE-FULL` equals the ticket count.
Ready-check failures stay near about 4% at `ready_fail_p` 0.2 and max visits 2.
`QUEUE-FULL` is a separate column. Search util/sat should move with the seven
pools; ready stays hot; mean latency stays in one band once ready is past
capacity. After all scenarios, `results/summary.md`,
`sensitivity-servers.png`, and `sensitivity-latency.png` must exist.
