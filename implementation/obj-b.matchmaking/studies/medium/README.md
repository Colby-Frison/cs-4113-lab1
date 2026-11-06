# Medium matchmaking study

A queue is one stage of the ticket pipeline. Medium grows search by bracket:
$4$ regions $\times$ $2$ modes $\times$ $5$ skill bands at one replica, so
$40$ search shards plus the five shared stages ($45$ queues). That sits in the
$10$–$100$ band. The sink row is still the original ticket; the study does not
report MMR spread.

Runs and figures live here, one directory per server set. The comparison table
is [results/summary.md](results/summary.md).

## Metrics

Same reading as Small: time-to-match, USE, `QUEUE-FULL` vs ready-check failure,
throughput. `summary.md` keeps the two error kinds in separate columns.

## Queue layout

| Log ids | Stage | Workers in this study |
| --- | --- | --- |
| `q0` | admit | 1 |
| `q1` | rating | 1 |
| `q2` | route | 1 |
| `q3` … `q42` | search (one queue per bracket) | the pool under test (baseline $4$ each) |
| `q43` | ready | 1 |
| `q44` | allocate | 1 |

Route picks a search shard from the ticket's region, mode, and band. Ready
retries return to the same shard. Ready writes `failed.csv` at the visit cap;
allocate writes `matched.csv`.

## Configuration

```text
python implementation/obj-b.matchmaking/studies/run_study.py --size medium
bash implementation/obj-b.matchmaking/studies/remote/run_medium.sh
```

Backend is one process pair per queue ($90$ processes). gRPC after admit; POSIX
FIFO into admit. Linux (or WSL) is required for that FIFO. Pattern defaults:
$10{,}000$ tickets, `--time-scale 0.02`, seed $7$, search swept only.

## Workload

Unscaled search mean is $11.45$ s with expected visits $1.20$. Forty shards at
$4$ workers and target $\rho = 0.8$ give interarrival

$$
\frac{11.45 \times 1.20}{0.8 \times 4 \times 40} = 0.107 \text{ s}.
$$

The same arrival puts the single ready worker near $\rho = 22.35$ (fixed $2$ s
ready wait, same $1.20$ visits). Every buffer holds $10{,}000$ tickets so a
slow stage queues instead of dropping.

## What the seven sets did

Search is the swept resource (baseline $4$ per shard; totals $112$–$320$).
Shared stages stay at $1$. Measured on the local WSL sweep:

| Scenario | Search workers | Matched | Ready-fail | `QUEUE-FULL` | Mean latency (ms) | Search peak sat |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `base` | 160 | 9609 | 391 | 0 | 292457 | 3.6 |
| `srv-10` | 144 | 9607 | 393 | 0 | 287712 | 4.4 |
| `srv-20` | 128 | 9609 | 391 | 0 | 280781 | 5.5 |
| `srv-30` | 112 | 9607 | 393 | 0 | 280700 | 5.5 |
| `srv+25` | 200 | 9622 | 378 | 0 | 284395 | 2.5 |
| `srv+50` | 240 | 9606 | 394 | 0 | 285588 | 1.6 |
| `srv+100` | 320 | 9598 | 402 | 0 | 289153 | 1.5 |

Every ticket reached a sink. `QUEUE-FULL` is $0$. Ready-check failures stay
near $3.8$–$4.0\%$ of tickets. Search peak saturation falls as the pool grows
($5.5$ at the smallest pool, $1.5$ at double), so the sweep does move the
search line. Mean time-to-match does not: it stays in a tight band around
$281$–$292$ s (~$4.7$–$4.9$ min) in every set. Ready peak utilization is pegged
high in every set. Extra search shards clear search; they do not clear ready.

Throughput and latency are therefore ready-limited. That is the Medium
amplification of the Small result: sharding search by bracket while leaving
one ready worker multiplies offered load on ready (~$\rho \approx 22$) without
giving that stage any new capacity.

## Does this match matchmaking?

Bracket fan-out is the right growth step for Medium: different region / mode /
band tickets land on different search queues. The measurement that survives is
not “more search always helps,” but “a shared post-search gate becomes the
product bottleneck once arrival is sized for many shards.” Staffing or
sharding ready with the search fan-out is the practitioner move. A product
ready depth much smaller than $10{,}000$ would have turned this backlog into
`QUEUE-FULL` instead of multi-minute time-to-match.

## Takeaway

Do not scale search alone when ready is shared and already past capacity.
Medium makes that visible: search saturation responds to the sweep; latency
does not. Staff ready (or replicate it) before buying more search workers.
Keep `QUEUE-FULL` and ready-check failures in different columns.

Large keeps the same shared ready stage and multiplies search again with
replicas; expect the same qualitative story unless ready is resized with the
pattern.
