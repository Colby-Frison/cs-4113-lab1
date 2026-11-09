# KV store N x M study

Stateful visit-count KV under hash sharding ($N$) and optional 2PC replication ($M$).
Each scenario offers 80 jobs with fixed wait 0.020s, queue depth 200, 2 gRPC workers per process.
Jobs are partitioned by `crc32(job_id) % N`. When $M \ge 2$, each shard
coordinator runs Prepare/Commit against $M$ replicas before updating its map.

| scenario | N | M | delivered | dropped | mean latency (ms) | peak utilization | peak saturation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| n1-m1 | 1 | 1 | 80 | 0 | 529.5 | 1.000 | 69.0 |
| n2-m1 | 2 | 1 | 80 | 0 | 255.3 | 1.000 | 35.2 |
| n4-m1 | 4 | 1 | 80 | 0 | 142.3 | 1.000 | 17.6 |
| n1-m2 | 1 | 2 | 80 | 0 | 736.9 | 1.000 | 71.6 |
| n1-m4 | 1 | 4 | 80 | 0 | 925.5 | 1.000 | 73.5 |
| n2-m2 | 2 | 2 | 80 | 0 | 411.5 | 1.000 | 37.3 |
| n4-m2 | 4 | 2 | 80 | 0 | 240.5 | 1.000 | 17.5 |

## How to read the plots

- **Utilization / saturation / errors** are per shard queue (`q0` highlighted).
- **Latency / throughput** are end-to-end across the system.
- Raising $N$ parallelizes independent shard queues (more capacity for the same job count).
- Raising $M$ adds 2PC round-trips on every visit increment, which usually increases
  visit latency even when delivery succeeds.

Six plots per scenario live in `results/<scenario>/`. Accessory tool output is under
`results/<scenario>/tools/`. Raw `runs/` are gitignored.
