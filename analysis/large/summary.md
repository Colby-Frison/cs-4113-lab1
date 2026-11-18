# Medium wait-job sanity check

128 independent queue systems. Each job is one wait of 0.050s.
All queues run simultaneously.
Each scenario offers 12800 jobs (100 per queue), queue depth 100.
Backend: shared-process gRPC.
Servers are the resource under test. Time in the plots is milliseconds from the first event.

| scenario | server factor | servers | per queue | delivered | dropped | mean latency (ms) | peak utilization | peak saturation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base | 1.00 | 512 | 4 | 12800 | 0 | 211.7 | 0.100 | 0.5 |
| srv-10 | 0.90 | 461 | 3–4 | 12800 | 0 | 217.3 | 0.133 | 0.5 |
| srv-20 | 0.80 | 410 | 3–4 | 12800 | 0 | 220.3 | 0.133 | 0.5 |
| srv-30 | 0.70 | 358 | 2–3 | 12800 | 0 | 217.2 | 0.200 | 0.5 |
| srv+25 | 1.25 | 640 | 5 | 12800 | 0 | 211.2 | 0.080 | 0.5 |
| srv+50 | 1.50 | 768 | 6 | 12800 | 0 | 216.5 | 0.067 | 0.5 |
| srv+100 | 2.00 | 1024 | 8 | 12800 | 0 | 219.1 | 0.050 | 0.5 |

Six plots per scenario, in `results/<scenario>/`:

- `utilization.png`, `saturation.png`, `errors.png`
- `latency.png`, `throughput.png`
- `longest-jobs.png` (three longest jobs)

Each `results/<scenario>/tools/` directory holds the accessory figures
(`use.png`, `resplot.png`, the response plots, `etrace.png`).
Logs and CSVs are written there on a re-run and are gitignored.
`etrace.png` is the first 40 jobs. `longest-jobs.png` is the long-job trace the rubric asks for.
