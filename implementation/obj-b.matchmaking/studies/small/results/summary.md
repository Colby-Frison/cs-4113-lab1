# small matchmaking study

6 queues: five shared stages and one search queue. Skill band still changes the wait.
Each scenario offers 1000 tickets.
Unscaled search mean is $11.45$ s and a ready retry brings the expected search visits to $1.20$.
The unscaled interarrival mean is $1.718$ s so one search queue with 10 workers sits near $\rho = 0.8$.
The same arrival puts the single ready worker near $\rho = 1.40$. Staff ready before adding search workers.
`--time-scale 0.02` shortens every sleep, including the interarrival.
Search and allocate are the pools under test. Every other stage has 1 worker.
Every buffer holds 1000 tickets, so a slower pool queues instead of dropping.
`QUEUE-FULL` is a ticket that never entered a stage. A ready-check failure is a ticket that reached the failed sink.
Time in the plots is milliseconds from the first event.
The USE panels draw one line per stage (1000-step window). Search is dark blue; ready is red.

| scenario | search workers | per queue | matched | ready-check failures | QUEUE-FULL | mean latency (ms) | search peak util | search peak sat | ready peak util |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base | 10 | 10 | 956 | 44 | 0 | 13063.1 | 0.990 | 9.3 | 0.900 |
| srv-10 | 9 | 9 | 955 | 45 | 0 | 12927.5 | 0.989 | 25.0 | 0.900 |
| srv-20 | 8 | 8 | 952 | 48 | 0 | 12579.7 | 1.000 | 55.3 | 0.900 |
| srv-30 | 7 | 7 | 952 | 48 | 0 | 16957.2 | 1.000 | 144.0 | 0.900 |
| srv+25 | 12 | 12 | 948 | 52 | 0 | 12679.4 | 0.983 | 3.9 | 0.900 |
| srv+50 | 15 | 15 | 948 | 52 | 0 | 12440.7 | 0.973 | 2.5 | 0.900 |
| srv+100 | 20 | 20 | 947 | 53 | 0 | 12578.9 | 0.765 | 1.0 | 0.900 |

Six plots per scenario, in `results/<scenario>/`:

- `utilization.png`, `saturation.png`, `errors.png` (`errors.png` counts `QUEUE-FULL` only)
- `latency.png`, `throughput.png`
- `longest-jobs.png` (three longest jobs)

`sensitivity-servers.png` compares the seven pools: mean latency, `QUEUE-FULL` and ready-check failures as a percent of tickets, and tickets completed per second. `sensitivity-latency.png` is that latency series as bars. The sets are ordered from the smallest search total to the largest.
