# medium matchmaking study

45 queues: five shared stages and 40 search shards (1 per bracket).
Each scenario offers 10000 tickets.
Unscaled search mean is $11.45$ s and a ready retry brings the expected search visits to $1.20$.
The unscaled interarrival mean is $0.107$ s so one search shard with 4 workers sits near $\rho = 0.8$.
The same arrival puts the single ready worker near $\rho = 22.35$. Staff ready before adding search workers.
`--time-scale 0.02` shortens every sleep, including the interarrival.
Search is the pool under test. Every other stage has 1 worker.
Every buffer holds 10000 tickets, so a slower pool queues instead of dropping.
`QUEUE-FULL` is a ticket that never entered a stage. A ready-check failure is a ticket that reached the failed sink.
Time in the plots is milliseconds from the first event.
The USE panels draw one line per stage (1000-step window). Search is dark blue; ready is red.

| scenario | search workers | per queue | matched | ready-check failures | QUEUE-FULL | mean latency (ms) | search peak util | search peak sat | ready peak util |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base | 160 | 4 | 9609 | 391 | 0 | 292456.8 | 1.350 | 3.6 | 2.500 |
| srv-10 | 144 | 3–4 | 9607 | 393 | 0 | 287711.8 | 1.425 | 4.4 | 2.600 |
| srv-20 | 128 | 3–4 | 9609 | 391 | 0 | 280780.9 | 1.767 | 5.5 | 2.600 |
| srv-30 | 112 | 2–3 | 9607 | 393 | 0 | 280699.8 | 1.567 | 5.5 | 2.600 |
| srv+25 | 200 | 5 | 9622 | 378 | 0 | 284394.5 | 1.520 | 2.5 | 2.600 |
| srv+50 | 240 | 6 | 9606 | 394 | 0 | 285587.9 | 1.233 | 1.6 | 2.600 |
| srv+100 | 320 | 8 | 9598 | 402 | 0 | 289152.5 | 0.825 | 1.5 | 2.600 |

Six plots per scenario, in `results/<scenario>/`:

- `utilization.png`, `saturation.png`, `errors.png` (`errors.png` counts `QUEUE-FULL` only)
- `latency.png`, `throughput.png`
- `longest-jobs.png` (three longest jobs)

`sensitivity-servers.png` compares the seven pools: mean latency, `QUEUE-FULL` and ready-check failures as a percent of tickets, and tickets completed per second. `sensitivity-latency.png` is that latency series as bars. The sets are ordered from the smallest search total to the largest.

## Analysis

Every ticket reached a sink (`QUEUE-FULL` = 0). Ready-check failures stay near
$4\%$ across the sweep. Search peak saturation falls as workers rise ($5.5$ at
`srv-30` down to $1.5$ at `srv+100`), so the search line does respond to the
knob. Mean time-to-match does not: it stays about $281$–$292$ s in every set.
Ready utilization stays pegged high throughout.

That is the Medium version of the Small lesson. Arrival is sized so one search
shard sits near $\rho = 0.8$, which drives the single ready worker near
$\rho = 22$. Sharding search by bracket without staffing ready multiplies load
on the shared gate. Practitioner takeaway: scale ready with search fan-out;
buying search alone will not move latency. Fuller write-up:
[../README.md](../README.md).
