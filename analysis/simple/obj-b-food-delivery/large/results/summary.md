# Large-study measured results

200 queues; 21 runs; 2,100,000 generated orders. These are measurements from this output directory.

| Scenario | Couriers | Delivered/s | Mean latency (s) | Loss (%) | Max P95 injection lag (s) | Lagged runs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 200 | 817.82 | 2.388 | 13.92 | 0.0010 | 0 |
| couriers-plus25 | 250 | 914.92 | 1.545 | 4.57 | 0.0010 | 0 |
| couriers-plus50 | 300 | 955.58 | 0.791 | 1.72 | 0.0011 | 0 |
| couriers-plus100 | 400 | 955.90 | 0.725 | 1.72 | 0.0011 | 0 |
| couriers-minus10 | 180 | 754.32 | 2.802 | 19.58 | 0.0010 | 0 |
| couriers-minus20 | 160 | 699.42 | 3.270 | 25.41 | 0.0010 | 0 |
| couriers-minus30 | 140 | 625.59 | 3.852 | 32.14 | 0.0010 | 0 |

Each scenario folder has six main plots plus Objective A accessory figures in tools/.
Latency excludes rejected orders. Error bars are seed min–max, not confidence intervals.
LAGGED means P95 injection delay exceeds the configured 10 ms threshold; such runs are retained and cannot be treated as exact arrival replay.
All 200 queues appear in USE heatmaps; regional line panels show R0–R4 as a readable sample. CSVs retain all 50 regions.
The 100 ms accessory samples give 1/10/100-second rolling windows; main USE integrates event states.
