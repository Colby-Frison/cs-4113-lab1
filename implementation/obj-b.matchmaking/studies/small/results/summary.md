# Small matchmaking study

Six stage queues (`q0` admit through `q5` allocate). Each scenario offers 1000 tickets.
Unscaled search mean is $11.45$ s and a ready retry brings the expected search visits to $1.20$.
The unscaled interarrival mean is $1.718$ s so a search pool of 10 sits near $\rho = 0.8$.
The same arrival puts the single ready worker near $\rho = 1.40$.
`--time-scale 0.02` shortens every sleep, including the interarrival.
Search and allocate are the pools under test. Every other stage has 1 worker.
Every buffer holds 1000 tickets, so a slower pool queues instead of dropping.
`QUEUE-FULL` is a ticket that never entered a stage. A ready-check failure is a ticket that reached the failed sink.
Time in the plots is milliseconds from the first event.

| scenario | workers | matched | ready-check failures | QUEUE-FULL | mean latency (ms) | search peak util | search peak sat | ready peak util |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base | 10 | 957 | 43 | 0 | 15159.4 | 0.980 | 12.8 | 0.900 |
| srv-10 | 9 | 948 | 52 | 0 | 14319.8 | 0.989 | 15.6 | 0.900 |
| srv-20 | 8 | 949 | 51 | 0 | 24328.3 | 0.988 | 20.5 | 0.900 |
| srv-30 | 7 | 951 | 49 | 0 | 21071.4 | 1.000 | 88.3 | 0.900 |
| srv+25 | 12 | 955 | 45 | 0 | 20056.2 | 0.983 | 3.0 | 0.900 |
| srv+50 | 15 | 948 | 52 | 0 | 21801.6 | 0.967 | 2.0 | 0.900 |
| srv+100 | 20 | 952 | 48 | 0 | 19035.2 | 0.730 | 1.0 | 0.900 |

Six plots per scenario, in `results/<scenario>/`:

- `utilization.png`, `saturation.png`, `errors.png` (`errors.png` counts `QUEUE-FULL` only)
- `latency.png`, `throughput.png`
- `longest-jobs.png` (three longest jobs)

The dark line on the USE panels is `q3` search. The other five queues are pale.
Error and throughput charts start at zero.

Each `results/<scenario>/tools/` directory holds the accessory figures
(`use.png`, `resplot.png`, the response plots, `etrace.png`) and `q3-qmet.csv`.
Logs and CSVs there are gitignored.
`etrace.png` is the first 40 jobs. `longest-jobs.png` is the long-job trace.

`sensitivity-servers.png` compares the seven pools on one chart: mean latency, `QUEUE-FULL` and ready-check failures as a percent of tickets, and tickets completed per second. `sensitivity-latency.png` is that latency series as bars. The sets are ordered from the smallest pool to the largest.
