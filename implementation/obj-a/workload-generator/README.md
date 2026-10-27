# Workload generator

Streams wait-task jobs as a Lab 0-shaped CSV. Each task is a sleep in seconds (`W`), not a discrete simulator step. This program only writes rows. It does not log Lab 0 events and it does not open a gRPC port. The queue is what reads the file.

Part of the [Objective A framework](../README.md).

## How a run proceeds

1. Parse the two distributions and the stop limits. A missing statistic exits before any row is written.
2. Write `Job Id,arrival time,Tasks` and flush, so a FIFO reader sees the header immediately.
3. Draw an arrival delay and sleep it. This happens before every job, including the first. If `--duration` runs out during that sleep, the loop stops and does not write a partial row.
4. Draw `--tasks` waits (default 1) from `--wait-dist`. Task ids are `0, 1, ...` in that order.
5. Write one CSV row whose arrival column is `time.time()` at the moment of the write, then flush.
6. Repeat until `--count` jobs have been written, `--duration` has elapsed, or the process receives Ctrl-C. Ctrl-C closes the file and exits 0. If both limits are set, whichever is reached first wins. The count is checked at the top of the loop, so the process does not sleep an extra arrival after the last job.

`fixed`, `uniform`, and `normal` are fractional seconds. A normal draw below 0 is clamped to 0. `geometric` counts failures before the first success, so 0 seconds is possible, and that integer is the sleep. `poisson` is Knuth's sampler and is used the same way. One `random.Random` serves both distributions, so `--seed` fixes the whole stream.

```bash
python workload_generator.py
  --arrival-dist DIST [arrival stats]
  --wait-dist DIST [wait stats]
  [--tasks N]
  [--count N]
  [--duration SEC]
  [--seed N]
  [--out PATH]
```

`--arrival-dist` is the pause before each job, including the first. `--wait-dist` is drawn once per task. `--tasks` is the number of waits on every job (default 1).

The header `Job Id,arrival time,Tasks` is written once. Each data row is flushed. Arrival time is `time.time()` when the row is emitted. Without `--out`, rows go to stdout.

The process runs until Ctrl-C. `--count` and `--duration` stop early. If both are set, the first limit reached wins. A duration that expires during the arrival sleep does not emit a partial job.

## Distributions

`fixed`, `uniform`, and `normal` are fractional seconds. `normal` is clamped at 0. `geometric` and `poisson` return a non-negative integer, and that integer is the number of seconds.

| `--arrival-dist` / `--wait-dist` | Required flags |
| --- | --- |
| `fixed` | `--arrival-fixed` / `--wait-fixed` seconds, `>= 0` |
| `uniform` | `--arrival-low` / `--arrival-high` (and the `--wait-` pair), `0 <= low <= high` |
| `normal` | `--arrival-mu` / `--arrival-sigma` (and the `--wait-` pair), sigma `>= 0` |
| `geometric` | `--arrival-p` / `--wait-p` in `(0, 1]` |
| `poisson` | `--arrival-lam` / `--wait-lam`, `>= 0` |

## Example

```bash
python workload_generator.py \
  --arrival-dist poisson --arrival-lam 0.1 \
  --wait-dist uniform --wait-low 0.01 --wait-high 0.05 \
  --tasks 2 --count 100 --seed 1 --out jobs.csv
```

A row looks like:

```text
j0,1760990100.123,"[""0: W 0.250000"", ""1: W 0.100000""]"
```
