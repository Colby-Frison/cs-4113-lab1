# Matchmaking workload generator

Writes party-ticket rows for the ranked 5v5 pipeline. A job is one party of 1 to 5 players. The Lab 0 CSV is unchanged: `Job Id`, `arrival time`, and six `tid: W seconds` tasks. Region, mode, skill band, and party size are in the job id (`t0.na.ranked.mid.p2`). Servers sleep the stored waits. They do not draw again.

Part of [the matchmaking application](../README.md). The draws are `mm/model.py`.

## Decisions

Interarrival is exponential. The flag `--arrival-mean` is the mean in matchmaking seconds, before scale. The default is $14$. One search server then sees offered load near $\rho \approx 0.8$, and the baseline search buffer of $32$ does not fill on a short run. `--seed` makes the draws repeatable. With neither `--count` nor `--duration`, the process runs until Ctrl-C. When both are set, `--count` stops the loop first. A duration that expires during the arrival sleep does not emit a partial row. The arrival sleep happens before every ticket, including the first.

Party size is categorical: $0.55$, $0.25$, $0.12$, $0.05$, $0.03$ for sizes $1$ through $5$. Solos and duos are the common case.

Region, when it is not pinned, is `na` $0.40$, `eu` $0.30$, `ap` $0.20$, `sa` $0.10$. Mode, when it is not pinned, is `ranked` $0.70$ and `unrated` $0.30$. The baseline topology pins `na` and `ranked`. The other tokens stay in the generator so a later topology can fan out without a new program.

Hidden skill is a standard normal draw. Bands are the cuts $-1.0$, $-0.2$, $0.6$, and $1.5$. `apex` is the upper tail.

| tid | Stage | Unscaled draw |
| --- | --- | --- |
| 0 | validate | uniform $0.05$ to $0.15$ |
| 1 | rating | probability $0.90$ uniform $0.05$ to $0.15$, otherwise uniform $0.40$ to $0.80$ |
| 2 | route | fixed $0.02$ |
| 3 | search | exponential, mean $12 / 8 / 6 / 14 / 28$ for placement, low, mid, high, apex |
| 4 | ready | fixed $2.0$ |
| 5 | allocate | uniform $0.40$ to $0.80$ |

A party of $3$ or more multiplies the search mean by $1.4$. `--time-scale` (default $0.05$) multiplies every draw, including the interarrival. The demo topology uses $0.02$ so a local run stays short. Scale $0$ writes zero waits, which is how the unit tests avoid sleeping.

## Flags

```text
python workload_generator.py
  [--out PATH]
  [--count N]
  [--duration SECONDS]
  [--seed N]
  [--time-scale X]
  [--arrival-mean SECONDS]
  [--region na|eu|ap|sa]
  [--mode ranked|unrated]
```

## Example

```text
python implementation/obj-b.matchmaking/workload-generator/workload_generator.py --region na --mode ranked --count 5 --time-scale 0.02 --seed 7 --out tickets.csv
```
