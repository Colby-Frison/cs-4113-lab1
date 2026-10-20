# Workload generator

Streams wait-task jobs as a Lab 0-shaped CSV. Each task is a sleep in seconds (`W`), not a discrete simulator step.

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
