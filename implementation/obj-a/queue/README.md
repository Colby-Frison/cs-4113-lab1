# Queue

Reads wait-task CSV jobs and hands them to one or more outfiles. Adapted from `example_code/02_producer_and_consumer_instrumented.py`.

Part of the [Objective A framework](../README.md). Shared row and event code: [lib/README.md](../lib/README.md).

## How a run proceeds

1. Require at least one input (`--infile` and/or `--ingrpc`) and one output (`--outfile` and/or `--outgrpc`).
2. Start a sidecar thread. Every 0.1 seconds it logs `metric-q-size` with `Queue.qsize()`. That number is jobs still sitting in this process. When a consumer has taken a job, it is already gone from the sample, even if the server function has not finished the visit.
3. Open every `--outfile` before producers start. A FIFO open blocks until some other process has the read end, so jobs are not admitted and then dropped while the next stage is still starting.
4. Start one file-consumer thread per outfile, and `workers` gRPC sender threads per `--outgrpc`. They all call `get` on the same buffer. Whichever thread wins the job delivers it.
5. Start one producer thread per `--infile`. Each line is parsed. The header and blank lines are skipped. `put_nowait` admits a job that fits, which logs `ARRIVE-VIA` with the infile's basename. `queue.Full` logs `ERROR QUEUE-FULL` and the line is discarded. The thread does not wait for a free slot.
6. If `--ingrpc` is set, bind those ports. Both `ServerToQueue` and `QueueToQueue` call the same `put_nowait` path, so a gRPC arrival follows the same full-buffer rule as a file arrival. The `source_edge` on the request becomes the `ARRIVE-VIA` label.
7. Join the file producers. If any listen port is open, keep running until Ctrl-C, because more RPCs can still arrive after the files hit EOF. Then stop the servers, put a single `None` on the buffer, and join the consumers. Each consumer that sees `None` puts it back and exits, so every consumer observes the same marker.

A file consumer appends the original CSV line and logs `DEPART-VIA` with that file's basename. A gRPC sender calls `Submit`. It logs `DEPART-VIA host:port` when the peer returns, including `accepted=false` (the peer already logged `QUEUE-FULL`). It does not log `DEPART-VIA` when the RPC itself fails.

A job that fits in `--qsize` is queued and logged `ARRIVE-VIA`. A job that does not fit is logged `ERROR QUEUE-FULL` and dropped. The producer does not block. Omit `--qsize` for an unbounded buffer. `--qsize` must be at least 1.

One consumer thread runs per `--outfile`. Whichever consumer takes the job writes the original CSV line and logs `DEPART-VIA` with that file's basename. `metric-q-size` is how many jobs are still in this buffer. Taking a job removes it from that sample; the server function is what logs the visit.

Outfiles are opened before producers start. On a FIFO that open waits until the reader is already up, so jobs are not dropped while the next process is starting.

gRPC is optional and per edge. `--ingrpc interface:port,workers` listens. `--outgrpc host:port,workers` sends to a server function. Add `,queue` to send to another queue. File and gRPC edges can be mixed. A listen port stays up until Ctrl-C. Details, startup order, and the three protos: [../proto/README.md](../proto/README.md).

## Events

The log prefix matches the course examples. The message body is the Lab 0 line. `time` is wall-clock seconds with 3 decimal places.

```text
{time} {job_id} IN {qid} ARRIVE-VIA {infile-basename}
{time} {job_id} IN {qid} DEPART-VIA {outfile-basename}
{time} {job_id} IN {qid} ERROR QUEUE-FULL
metric-q-size {n}
```

## Flags

```bash
python queue.py
  [-i INFILE ...]
  [-o OUTFILE ...]
  [--ingrpc interface:port,workers ...]
  [--outgrpc host:port,workers[,server|queue] ...]
  [--qsize N]
  [--qid q0]
  [--logfile PATH]
```

At least one input (`--infile` or `--ingrpc`) and one output (`--outfile` or `--outgrpc`) are required.

## FIFO order

Start the server function (the reader) before the queue. The queue blocks in `open` on the FIFO until that reader exists.

```bash
mkfifo /tmp/q_to_sf
python ../server-func-stateless/server_func.py -i /tmp/q_to_sf -o sink.csv --logfile sf.log &
python queue.py --qsize 10 -i jobs.csv -o /tmp/q_to_sf --logfile q.log
```

FIFOs are a bash / WSL arrangement. The file example in `../README.md` is the Windows check.
