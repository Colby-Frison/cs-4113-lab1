# large matchmaking study

205 queues: five shared stages and 200 search shards (5 per bracket).
Each scenario offers 100000 tickets.
Unscaled search mean is $11.45$ s and a ready retry brings the expected search visits to $1.20$.
The unscaled interarrival mean is $0.021$ s so one search shard with 4 workers sits near $\rho = 0.8$.
The same arrival puts the single ready worker near $\rho = 111.75$. Staff ready before adding search workers.
`--time-scale 0.02` shortens every sleep, including the interarrival.
Search is the pool under test. Every other stage has 1 worker.
Every buffer holds 100000 tickets, so a slower pool queues instead of dropping.
`QUEUE-FULL` is a ticket that never entered a stage. A ready-check failure is a ticket that reached the failed sink.
Time in the plots is milliseconds from the first event.
The USE panels draw one line per stage (1000-step window). Search is dark blue; ready is red.

| scenario | search workers | per queue | matched | ready-check failures | QUEUE-FULL | mean latency (ms) | search peak util | search peak sat | ready peak util |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base | 800 | 4 | 95976 | 4024 | 0 | 3407851.7 | 0.925 | 1.4 | 3.000 |
| srv-10 | 720 | 3–4 | 95980 | 4020 | 0 | 3259570.5 | 0.825 | 0.6 | 1.000 |
| srv-20 | 640 | 3–4 | 96027 | 3973 | 0 | 2626870.8 | 0.925 | 1.9 | 1.000 |
| srv-30 | 560 | 2–3 | 96034 | 3966 | 0 | 2679563.0 | 0.900 | 2.6 | 1.000 |
| srv+25 | 1000 | 5 | 95986 | 4014 | 0 | 2632996.4 | 0.900 | 0.6 | 1.000 |
| srv+50 | 1200 | 6 | 95969 | 4031 | 0 | 2684029.3 | 0.667 | 0.7 | 1.000 |
| srv+100 | 1600 | 8 | 96031 | 3969 | 0 | 3634736.8 | 0.487 | 0.6 | 2.000 |

Six plots per scenario, in `results/<scenario>/`:

- `utilization.png`, `saturation.png`, `errors.png` (`errors.png` counts `QUEUE-FULL` only)
- `latency.png`, `throughput.png`
- `longest-jobs.png` (three longest jobs)

`sensitivity-servers.png` compares the seven pools: mean latency, `QUEUE-FULL` and ready-check failures as a percent of tickets, and tickets completed per second. `sensitivity-latency.png` is that latency series as bars. The sets are ordered from the smallest search total to the largest.
