# Remote Large and Very Large runs

Small and Medium finish in a local VirtualBox Linux guest. Large (205 queues) and Very Large (1,005 queues) use the hosted pair and take much longer because the single ready worker is past capacity. Run those sizes on a Linux box with this kit. The shared runner is still [run_study.py](../run_study.py).

## Setup

1. Clone or copy the lab repository onto the Linux host.
2. Install Python 3.11+ and the Objective A deps from the repository root:

```text
python3 -m pip install -r implementation/obj-a/requirements.txt
python3 -m pip install matplotlib pandas
```

3. Free host ports from 29100 upward (each scenario takes a block of 200).
4. Prefer `tmux` or `screen` so a dropped SSH session does not kill the sweep.

## Run

From the repository root:

```text
bash implementation/obj-b.matchmaking/studies/remote/run_large.sh
bash implementation/obj-b.matchmaking/studies/remote/run_very_large.sh
```

Pass the same flags `run_study.py` accepts after the script name:

```text
bash implementation/obj-b.matchmaking/studies/remote/run_large.sh --only base
bash implementation/obj-b.matchmaking/studies/remote/run_very_large.sh --only base,srv-30
```

Stdout and stderr append to `studies/<size>/remote-run.log`. Live sinks:

```text
tail -f implementation/obj-b.matchmaking/studies/large/runs/base/matched.csv
tail -f implementation/obj-b.matchmaking/studies/large/remote-run.log
```

Pattern defaults are 100,000 tickets and `time_scale` 0.02. The arrival that puts one search shard near $\rho = 0.8$ leaves ready far past capacity, so each scenario can run for many hours. Plan one scenario overnight if you are unsure of the host.

## Pack results

After a size finishes and `summary.md` plus the sensitivity PNGs exist:

```text
bash implementation/obj-b.matchmaking/studies/remote/pack_results.sh large
bash implementation/obj-b.matchmaking/studies/remote/pack_results.sh very-large
```

That writes `studies/<size>/<size>-results.tar.gz` with every `results/**/*.png` and `results/summary.md`. Copy the archive off the box and unpack it into the matching `studies/<size>/` tree on the review branch. Do not commit `runs/` or result logs and CSVs.
