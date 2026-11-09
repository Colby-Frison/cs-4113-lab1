# very-large matchmaking study

1005 queues: five shared stages and 1000 search shards (25 per bracket).
Each scenario offers 100000 tickets.
Unscaled search mean is $11.45$ s and a ready retry brings the expected search visits to $1.20$.
The unscaled interarrival mean is $0.004$ s so one search shard with 4 workers sits near $\rho = 0.8$.
The same arrival puts the single ready worker near $\rho = 558.76$. Staff ready before adding search workers.
`--time-scale 0.02` shortens every sleep, including the interarrival.
Search is the pool under test. Every other stage has 1 worker.
Every buffer holds 100000 tickets, so a slower pool queues instead of dropping.
`QUEUE-FULL` is a ticket that never entered a stage. A ready-check failure is a ticket that reached the failed sink.
Time in the plots is milliseconds from the first event.
The USE panels draw one line per stage. The dark line is the busiest search queue.

| scenario | search workers | per queue | matched | ready-check failures | QUEUE-FULL | mean latency (ms) | search peak util | search peak sat | ready peak util |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base | 4000 | 4 | 96000 | 4000 | 0 | 3596364.9 | 0.325 | 0.6 | 1.000 |
| srv-10 | 3600 | 3–4 | 95971 | 4029 | 0 | 2770106.0 | 0.400 | 0.6 | 1.000 |
| srv-20 | 3200 | 3–4 | 95954 | 4046 | 0 | 2758518.3 | 0.467 | 0.6 | 1.000 |
| srv-30 | 2800 | 2–3 | 96035 | 3965 | 0 | 2758096.3 | 0.533 | 0.6 | 1.000 |
| srv+25 | 5000 | 5 | 95985 | 4015 | 0 | 4043809.2 | 0.220 | 0.6 | 1.000 |
| srv+50 | 6000 | 6 | 96029 | 3971 | 0 | 2761262.7 | 0.267 | 0.6 | 1.000 |
| srv+100 | 8000 | 8 | 96011 | 3989 | 0 | 2801293.0 | 0.200 | 0.6 | 1.000 |

Six plots per scenario, in `results/<scenario>/`:

- `utilization.png`, `saturation.png`, `errors.png` (`errors.png` counts `QUEUE-FULL` only)
- `latency.png`, `throughput.png`
- `longest-jobs.png` (three longest jobs)

`sensitivity-servers.png` compares the seven pools: mean latency, `QUEUE-FULL` and ready-check failures as a percent of tickets, and tickets completed per second. `sensitivity-latency.png` is that latency series as bars. The sets are ordered from the smallest search total to the largest.
