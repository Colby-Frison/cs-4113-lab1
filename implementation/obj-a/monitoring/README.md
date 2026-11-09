# Monitoring (timeseries metrics)

Live USE + throughput/latency for Objective A queues and server functions, using Prometheus and Grafana. Offline plots from Lab 0 logs (`analyze_run` → `compute-use` / `derive-sys-metrics`) stay the study source of truth. This stack is the live view during a run.

## Why Prometheus + Grafana

- **Pull scrapes** fit short-lived lab processes: each process exposes `/metrics` only when asked (`--metrics-port`). No agent push path to configure per machine.
- **Prometheus** stores the series and answers PromQL that matches USE and RED-style rates.
- **Grafana** is the dashboard layer the course links already use with Prometheus.
- Vendor APM would add accounts and agents without helping the post-run log analysis the studies already use.

References: [Prometheus + Grafana intro](https://medium.com/@dineshmurali/introduction-to-monitoring-with-prometheus-grafana-ea338d93b2d9), [visualizing with Grafana](https://prometheus.io/docs/tutorials/visualizing_metrics_using_grafana/), [Grafana Prometheus quickstart](https://grafana.com/docs/grafana/latest/getting-started/get-started-grafana-prometheus/), [USE / RED](https://betterstack.com/community/guides/monitoring/red-use-metrics/), [Python client](https://prometheus.github.io/client_python/instrumenting/).

## Collection design

Opt-in HTTP endpoint from `prometheus_client`:

```text
queue.py / server_func  --metrics-port 9100  →  GET /metrics
```

When the flag is omitted, counters are no-ops. The file-only path does not need gRPC. Studies keep their default launchers; they gain scrapes only if a runner passes `--metrics-port`.

Instrument shared Objective A (`queue.py`, `lib/server.py`). Matchmaking stages and hosted Large/VL already call `admit_job` / `serve_job`, so one layer covers sanity checks and domain apps.

### USE + Thr/Lat map

| Method | Queue | Server |
|--------|-------|--------|
| Utilization | (buffer has no servers) | `jobs_in_service / server_workers` |
| Saturation | `queue_depth`, `queue_depth_ratio` | (buffer lives in the queue) |
| Errors | `jobs_dropped_total{reason="queue_full"}` | `forward_errors_total` |
| Throughput | `jobs_admitted_total`, `jobs_departed_total` | `visits_total`, `tasks_completed_total` |
| Latency | `queue_wait_seconds` | `visit_seconds`, `task_wait_seconds` |

Labels: `qid`, and process role is implied by which series a target exports. Hosted Large/VL export many `qid` series from two scrapes (queue host + server host).

### Scrape shapes

- **One process per queue** (sanity, Small/Medium): one `/metrics` target per OS process.
- **Hosted** (Large/Very Large): two targets; gauges and counters are labeled by `qid`.
- **Compose demo** (baseline matchmaking): every queue and stage container listens on metrics port `9100` inside the network. Prometheus joins the app Compose network and scrapes by service name.

## Logs vs metrics

| | Lab 0 logs | Prometheus |
|--|------------|------------|
| Purpose | Post-run USE/Thr/Lat plots and study `summary.md` | Live dashboard while processes run |
| Path | `logger.info` → analyze tools | `--metrics-port` → scrape |
| Required for studies | Yes | No |

## Build note

The Compose image build context is `implementation/`. Study trees under
`obj-b.matchmaking/studies` are multi-GB (`large` alone is several GB of logs).
[`.dockerignore`](../../.dockerignore) excludes those trees so a rebuild does not
pull them into Docker Desktop (that has crashed the engine when the context
grew to multi-GB). Rebuild only when the large/very-large plot jobs are idle if
you are short on RAM.

## Deploy the demo dashboard

1. Start the matchmaking gRPC stack (metrics ports are wired by `orchestration/convert.py`):

```text
python implementation/obj-b.matchmaking/orchestration/run.py --continuous
```

2. Start Prometheus + Grafana from this directory (Docker must see the `matchmaking-grpc_default` network):

```text
docker compose -f implementation/obj-a/monitoring/docker-compose.yml up -d
```

3. Open Grafana at `http://localhost:3000` (anonymous Viewer). The provisioned **USE + Thr/Lat** dashboard loads automatically against Prometheus.

4. Stop:

```text
docker compose -f implementation/obj-a/monitoring/docker-compose.yml down
python implementation/obj-b.matchmaking/orchestration/run.py --down
```

For a host-only scrape (no shared Docker network), publish the mapped host metrics ports from Compose and point `prometheus.yml` at `host.docker.internal:<port>` instead.
