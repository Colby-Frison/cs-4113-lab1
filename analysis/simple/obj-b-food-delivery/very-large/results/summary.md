# Very-large study measured results

2000 queues; 7 runs; 700,000 generated orders. These are measurements from this output directory.

| Scenario | Couriers | Delivered/s | Mean latency (s) | Loss (%) | Max P95 injection lag (s) | Lagged runs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | 2000 | 634.44 | 14.467 | 1.91 | 0.0062 | 0 |
| couriers-plus25 | 2500 | 713.70 | 8.201 | 0.16 | 0.0059 | 0 |
| couriers-plus50 | 3000 | 763.92 | 5.974 | 0.15 | 0.0068 | 0 |
| couriers-plus100 | 4000 | 762.74 | 5.615 | 0.16 | 0.0068 | 0 |
| couriers-minus10 | 1800 | 559.67 | 17.763 | 5.05 | 0.0055 | 0 |
| couriers-minus20 | 1600 | 531.71 | 22.483 | 8.72 | 0.0076 | 0 |
| couriers-minus30 | 1400 | 457.95 | 26.045 | 12.78 | 0.0055 | 0 |

Each scenario folder has six main plots plus Objective A accessory figures in tools/.
Latency excludes rejected orders. Single seed: no uncertainty estimate.
LAGGED means P95 injection delay exceeds the configured 10 ms threshold; such runs are retained and cannot be treated as exact arrival replay.
All 2000 queues appear in USE heatmaps; regional line panels show R0–R4 as a readable sample. CSVs retain all 500 regions.
Accessory samples are 1000 ms apart; 10/100/1000-row windows cover 10/100/1000 seconds. Main USE integrates event states.
