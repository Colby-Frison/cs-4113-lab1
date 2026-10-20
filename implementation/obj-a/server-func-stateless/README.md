# Stateless server function

Reads wait-task jobs, sleeps each `W` seconds in order, and forwards the original CSV line on one outfile chosen at random. Nothing about the visit is stored.

gRPC flags exit with "not implemented".

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

If `--outfile` is omitted, the visit still runs and the last event is `ERROR NO-OUT-EDGE`. A line that is not a job is logged as an error and skipped. It is not a Lab 0 event.

## Flags

```bash
python server_func.py
  -i INFILE
  [-o OUTFILE ...]
  [--qid q0]
  [--server-id 0]
  [--logfile PATH]
```

## Example

```bash
python server_func.py -i queued.csv -o sink.csv --logfile sf.log --qid q0
```

For a FIFO, start this process before the queue. See `../queue/README.md`.
