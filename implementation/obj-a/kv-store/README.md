# Key-value store (tech-lead stretch)

Extends the [stateful server function](../server-func-stateful/README.md) visit
map into a durable, shardable, optionally replicated KV. Jobs still forward
`raw_line` unchanged. Analysis reuses the Objective A log tools.

## What each piece does

| Piece | Role |
| --- | --- |
| `../lib/kv_store.py` | JSON load/save (`--load-kv-from` / `--store-kv-to`) |
| `../lib/kv_routing.py` | Stable `crc32(key) % N` shard index |
| `../lib/kv_2pc.py` | Coordinator Prepare / Commit / Abort client |
| `replica.py` | Replica process answering the KV gRPC service |
| `topology.py` / `launch.py` | Build and run an $N \times M$ topology |
| `smoke_check.py` | Short end-to-end check for several $(N,M)$ pairs |
| `studies/run_kv_study.py` | USE + Thr/Lat sweep over $N$ and $M$ |

## Persistence

```bash
python implementation/obj-a/server-func-stateful/server_func.py \
  -i queued.csv -o sink.csv --logfile sf.log \
  --load-kv-from kv.json --store-kv-to kv.json
```

The file is a JSON object of `job_id -> visit count`. The map is rewritten after
each successful increment so a hard kill still leaves a usable file.

## Sharding ($N \ge 2$)

Jobs are partitioned with `shard_index(job_id, N)`. `launch.py` writes one CSV
per shard and starts one queue + stateful SF pair per shard.

```bash
python implementation/obj-a/kv-store/launch.py --run-dir /tmp/kv-n2 --shards 2 --replicas 1 --jobs 40
```

## Replication with 2PC ($M \ge 2$)

Each shard coordinator passes `--kv-replicas host:port,...`. On every visit
increment it runs Prepare on all replicas, then Commit (or Abort). Replica
processes:

```bash
python implementation/obj-a/kv-store/replica.py --listen 127.0.0.1:28000,2 --store-kv-to r0.json
```

## Combined $N \times M$

```bash
python implementation/obj-a/kv-store/smoke_check.py --configs "2,2;4,2"
```

## USE + Thr/Lat study

```bash
python implementation/obj-a/kv-store/studies/run_kv_study.py
# or a subset:
python implementation/obj-a/kv-store/studies/run_kv_study.py --only n1-m1,n2-m1,n1-m2
```

Figures and `summary.md` land under `studies/results/`. `studies/runs/` is
gitignored.
