# Queue

Reads wait-task CSV jobs and hands them to one or more outfiles. Adapted from `example_code/02_producer_and_consumer_instrumented.py`.

A job that fits in `--qsize` is queued and logged `ARRIVE-VIA`. A job that does not fit is logged `ERROR QUEUE-FULL` and dropped. The producer does not block. Omit `--qsize` for an unbounded buffer. `--qsize` must be at least 1.

One consumer thread runs per `--outfile`. Whichever consumer takes the job writes the original CSV line and logs `DEPART-VIA` with that file's basename. `metric-q-size` is how many jobs are still in this buffer. Taking a job removes it from that sample; the server function is what logs the visit.

Outfiles are opened before producers start. On a FIFO that open waits until the reader is already up, so jobs are not dropped while the next process is starting.

gRPC flags (`--ingrpc`, `--outgrpc`) exit with "not implemented".

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
  -i INFILE [-i INFILE ...]
  -o OUTFILE [-o OUTFILE ...]
  [--qsize N]
  [--qid q0]
  [--logfile PATH]
```

## FIFO order

Start the server function (the reader) before the queue. The queue blocks in `open` on the FIFO until that reader exists.

```bash
mkfifo /tmp/q_to_sf
python ../server-func-stateless/server_func.py -i /tmp/q_to_sf -o sink.csv --logfile sf.log &
python queue.py --qsize 10 -i jobs.csv -o /tmp/q_to_sf --logfile q.log
```

FIFOs are a bash / WSL arrangement. The file example in `../README.md` is the Windows check.
