# Architecture

A party ticket is one job. Six stages run in order. The monolith performs every stage inside one server visit. The staged system gives each stage its own queue process and its own server process. Decomposition does not change the CSV row. Each stage sleeps only its own task id and forwards `raw_line`.

```mermaid
qAdmit
sValidate
qRate
sRating
qRoute
flowchart LR
  gen[generator]
  qAdmit[qAdmit]
  sVal[sValidate]
  qRate[qRate]
  sRate[sRating]
  qRoute[qRoute]
  sRoute[sRoute]
  qSearch[qSearch]
  sSearch[sSearch]
  qReady[qReady]
  sReady[sReady]
  qAlloc[qAlloc]
  sAlloc[sAllocate]
  okSink[matched]
  failSink[failed]
  gen --> qAdmit --> sVal --> qRate --> sRate --> qRoute --> sRoute --> qSearch --> sSearch --> qReady --> sReady --> qAlloc --> sAlloc --> okSink
  sReady -->|ready fail| qSearch
  sReady -->|visit cap| failSink
```

The queue in front of a stage and the server that drains it share a queue id. Events for that hop are `IN` that id. Server count is the `workers` field on a gRPC listen port. The file demo does not open those ports. It still records `workers` so a later launch can.

## Job id

```text
t{seq}.{region}.{mode}.{band}.p{party}
```

| Field | Values |
| --- | --- |
| `seq` | Integer, unique in one generator run, starting at 0 |
| `region` | `na`, `eu`, `ap`, `sa` |
| `mode` | `ranked`, `unrated` |
| `band` | `placement`, `low`, `mid`, `high`, `apex` |
| `party` | `1` through `5` |

Example: `t0.na.ranked.mid.p2`.

Hidden skill is a standard normal draw, cut into bands at $-1.0$, $-0.2$, $0.6$, and $1.5$. `apex` is the upper tail. The band is chosen when the row is written. Routing reads it back from the job id. The row's task list is only waits.

## Stages

Waits below are matchmaking seconds before `--time-scale`. The generator multiplies every draw, including the interarrival, by that scale. A server sleeps the stored `W`.

| tid | Stage | Service draw | Where the ticket goes |
| --- | --- | --- | --- |
| 0 | validate | uniform $0.05$ to $0.15$ | the single downstream edge |
| 1 | rating | with probability $0.90$, uniform $0.05$ to $0.15$; otherwise a cold read, uniform $0.40$ to $0.80$ | the single downstream edge |
| 2 | route | fixed $0.02$ | the edge named by the region token |
| 3 | search | exponential, mean from the table below | the single downstream edge |
| 4 | ready | fixed $2.0$ | allocate on a pass; search on a fail; the failed sink when the visit cap is already reached |
| 5 | allocate | uniform $0.40$ to $0.80$ | the matched sink |

Search means, in unscaled seconds:

| Band | Mean |
| --- | --- |
| placement | $12$ |
| low | $8$ |
| mid | $6$ |
| high | $14$ |
| apex | $28$ |

A party of $3$ or more multiplies that mean by $1.4$. Larger parties and the apex band stay in search longer. With the band cuts and the party mix, the unscaled search mean is about $11.5$ seconds. Validate, rating, route, ready, and allocate together average under $3$ seconds, so search dominates time-to-match.

The baseline ready-check failure probability is $0.2$. `--max-ready-visits` is $2$. The ready server counts visits to that job id in the process, and the file demo persists the count in a state file so a later pass still sees it. Visit $1$ can return to search. A failure on visit $2$ goes to the failed sink. A pass on any visit goes to allocate.

The baseline route table has one entry, `na` to the search queue. A ticket whose region has no edge finishes the route visit and logs `ERROR NO-OUT-EDGE`.

## Topology file

[topology/baseline.json](topology/baseline.json) lists, in pipeline order, each queue (`id`, `qsize`, `workers`) and the server attached to it (`stage`, `server_id`, `workers`, and the route or ready edges). [topology/monolith.json](topology/monolith.json) is the one-queue form of the same workload. `run_demo.py` is a file-edge runner for those two files. A later orchestrator can read the same fields and emit a shell script, a FIFO Compose file, or a gRPC Compose file. Hostnames are not compiled into the servers. An edge is a path or `host:port`.

The staged file runner writes one CSV per hop under its work directory. Ready-check retries are a new search-queue input each round, so a finished round is not read again. `matched.csv` and `failed.csv` are the sinks. Every generated ticket should land in one of those two files.

## Processes and later containers

One queue process and one server process per stage. The C++port is one binary,++ `match_server`++, and++ `--stage` ++selects the visit. A container image can be that binary with different arguments. Python stages remain the reference for log lines and for the Windows file demo. The C++ server speaks the Objective A protos (`job.proto`, `queue_to_server.proto`, `server_to_queue.proto`) and writes the same `sv_pid` prefix and Lab 0 body.
