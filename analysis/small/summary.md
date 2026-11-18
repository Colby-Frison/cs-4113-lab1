# Medium wait-job sanity check

6 independent queue systems. Each job is one wait of 0.050s.
Each scenario offers 600 jobs (100 per queue), queue depth 100.
Servers are the resource under test. Time in the plots is milliseconds from the first event.

| scenario | server factor | servers | per queue | delivered | dropped | mean latency (ms) | peak utilization | peak saturation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base | 1.00 | 24 | 4 | 600 | 0 | 638.8 | 1.000 | 90.7 |
| srv-10 | 0.90 | 22 | 3–4 | 600 | 0 | 717.5 | 1.000 | 92.1 |
| srv-20 | 0.80 | 19 | 3–4 | 600 | 0 | 823.8 | 1.000 | 91.9 |
| srv-30 | 0.70 | 17 | 2–3 | 600 | 0 | 935.6 | 1.000 | 94.4 |
| srv+25 | 1.25 | 30 | 5 | 600 | 0 | 508.7 | 1.000 | 89.0 |
| srv+50 | 1.50 | 36 | 6 | 600 | 0 | 420.0 | 1.000 | 87.6 |
| srv+100 | 2.00 | 48 | 8 | 600 | 0 | 315.9 | 1.000 | 83.1 |

Six plots per scenario, in `results/<scenario>/`:

- `utilization.png`, `saturation.png`, `errors.png`
- `latency.png`, `throughput.png`
- `longest-jobs.png` (three longest jobs)

Each `results/<scenario>/tools/` directory holds the accessory figures
(`use.png`, `resplot.png`, the response plots, `etrace.png`).
Logs and CSVs are written there on a re-run and are gitignored.
`etrace.png` is the first 40 jobs. `longest-jobs.png` is the long-job trace the rubric asks for.
