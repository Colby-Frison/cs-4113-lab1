# Medium wait-job sanity check

100 independent queue systems. Each job is one wait of 0.050s.
Each scenario offers 10000 jobs (100 per queue), queue depth 100.
Servers are the resource under test. Time in the plots is milliseconds from the first event.

| scenario | server factor | servers | per queue | delivered | dropped | mean latency (ms) | peak utilization | peak saturation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| base | 1.00 | 400 | 4 | 10000 | 0 | 713.8 | 1.000 | 92.8 |
| srv-10 | 0.90 | 360 | 3–4 | 10000 | 0 | 863.9 | 1.000 | 96.9 |
| srv-20 | 0.80 | 320 | 3–4 | 10000 | 0 | 902.5 | 1.000 | 94.8 |
| srv-30 | 0.70 | 280 | 2–3 | 10000 | 0 | 1193.4 | 1.000 | 97.7 |
| srv+25 | 1.25 | 500 | 5 | 10000 | 0 | 535.4 | 1.000 | 88.9 |
| srv+50 | 1.50 | 600 | 6 | 10000 | 0 | 522.5 | 1.000 | 97.1 |
| srv+100 | 2.00 | 800 | 8 | 10000 | 0 | 394.8 | 1.000 | 97.1 |

Six plots per scenario, in `results/<scenario>/`:

- `utilization.png`, `saturation.png`, `errors.png`
- `latency.png`, `throughput.png`
- `longest-jobs.png` (three longest jobs)

Each `results/<scenario>/tools/` directory holds the accessory-tool outputs.
`etrace.png` is the first 40 jobs. `longest-jobs.png` is the long-job trace the rubric asks for.
