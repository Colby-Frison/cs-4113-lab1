# Stateless server function

Reads wait-task jobs, sleeps each `W` seconds in order, and forwards the original CSV line on one edge chosen at random. An edge is an `--outfile` or an `--outgrpc`. Nothing about the visit is stored.

Part of the [Objective A framework](../README.md). The visit itself is `serve_job` in [lib/server.py](../lib/server.py), shared with the stateful program. gRPC flags: [proto/README.md](../proto/README.md).

## How a visit runs

1. The input is exactly one of `--infile` or `--ingrpc`. Passing both exits before any job is read.
2. On the file path, every outfile is opened first, then each data row is parsed on this thread. The header is skipped. A row that is not a job is logged as an ordinary error and skipped. That line is not a Lab 0 event.
3. `ENTERS-SERVER` is logged with `--server-id` (default `0`).
4. For each task, in listed order: log `TASK-START`, sleep `W` seconds on this thread, log `TASK-END`. Each of those calls `time.time()`, so the timestamps bracket the sleep.
5. Log `EXITS-SERVER`.
6. If there is no outfile and no `--outgrpc`, log `ERROR NO-OUT-EDGE` and do not write the job.
7. Otherwise pick one edge uniformly. A file edge appends `raw_line` and flushes, then logs `DEPART-VIA` with the basename. A gRPC edge calls `Submit` on that queue and logs `DEPART-VIA host:port` when the peer returns, including a full-buffer rejection. A failed RPC logs an error and does not log `DEPART-VIA`. The next hop still receives the original row, not a rewritten one.

On `--ingrpc`, the same visit runs inside the `Submit` handler, so the queue that called this process stays blocked until the sleeps finish. That is the backpressure. The process then waits until Ctrl-C. `workers` is the size of that handler pool, so that many visits can sleep at once. There is no visit dictionary in this program.

The input is one of `--infile` or `--ingrpc`, not both. `--outgrpc host:port,workers` calls a queue (server-to-queue). File and gRPC outputs can be mixed. A listen port stays up until Ctrl-C. See [../proto/README.md](../proto/README.md).

## Events

```text
{time} {job_id} IN {qid} ENTERS-SERVER {server-id}
{time} {job_id} IN {qid} TASK-START {tid} "W {seconds}"
{time} {job_id} IN {qid} TASK-END {tid} "W {seconds}"
{time} {job_id} IN {qid} EXITS-SERVER {server-id}
{time} {job_id} IN {qid} DEPART-VIA {outfile-basename}
{time} {job_id} IN {qid} ERROR NO-OUT-EDGE
```

`TASK-END` is Lab 0's name for the end of a task. Each event gets its own wall-clock timestamp, so the sleep sits between `TASK-START` and `TASK-END`.

If there is no `--outfile` and no `--outgrpc`, the visit still runs and the last event is `ERROR NO-OUT-EDGE`. A line that is not a job is logged as an error and skipped. It is not a Lab 0 event. `DEPART-VIA` for a gRPC edge is `host:port`.

## Flags

```bash
python server_func.py
  (-i INFILE | --ingrpc interface:port,workers)
  [-o OUTFILE ...]
  [--outgrpc host:port,workers ...]
  [--qid q0]
  [--server-id 0]
  [--logfile PATH]
```

## Example

```bash
python server_func.py -i queued.csv -o sink.csv --logfile sf.log --qid q0
```

For a FIFO, start this process before the queue. See `../queue/README.md`.
