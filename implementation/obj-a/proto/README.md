# FIFO or gRPC

Each edge is either a file/FIFO or a gRPC address. File edges are the default. Add `--ingrpc` or `--outgrpc` only on the hops that should use RPC. A queue may mix the two. A server function has one input, and that input is either `--infile` or `--ingrpc`.

Part of the [Objective A framework](../README.md). Queue behavior: [queue/README.md](../queue/README.md). Server-function visit: [server-func-stateless/README.md](../server-func-stateless/README.md).

## How one Submit is handled

The sender builds a `Job` message from the CSV row (`raw_line` plus the structured fields) and sets `source_edge` to its own queue id. `Submit` is unary.

On a queue listen port, the handler calls the same `put_nowait` path as a file producer. Success returns `accepted=true` and logs `ARRIVE-VIA` with `source_edge`. A full buffer returns `accepted=false` and logs `ERROR QUEUE-FULL`. Both queue-ingress services (`ServerToQueue` and `QueueToQueue`) share that handler and that buffer, which is why one `--ingrpc` port accepts jobs from a server function and from another queue.

On a server-function listen port, the handler runs the whole visit, including the sleeps, before it returns `accepted=true`. The queue sender thread stays inside the RPC for that time. There is no second buffer inside the server function, so it does not return `QUEUE-FULL`.

The sender logs `DEPART-VIA` after the response, whether or not `accepted` is true, because the job has left this process. If the RPC raises, the sender logs an error and does not log `DEPART-VIA`. The job is not retried.

Install the Python packages before using an RPC edge. The file-only path does not import them.

```text
pip install -r implementation/obj-a/requirements.txt
```

Generated stubs live in `../lib/proto_gen/`. After editing a `.proto`, regenerate them:

```text
python implementation/obj-a/proto/generate_stubs.py
```

The checked-in stubs were generated with grpcio 1.84. They refuse to load on an older grpcio.

`kv_replica.proto` is the optional two-phase-commit service used by the
[KV store stretch](../kv-store/README.md). Job-forwarding edges still use the
three Submit protos above.

## Which flag is which

| Flag | Who | Form | What it does |
| --- | --- | --- | --- |
| `--infile` / `--outfile` | queue or server function | path | File or FIFO, same as the course examples |
| `--ingrpc` | queue or server function | `interface:port,workers` | Listen. Example: `0.0.0.0:50051,4` |
| `--outgrpc` | queue | `host:port,workers` or `host:port,workers,queue` | Default calls a server function. The trailing `queue` calls another queue. |
| `--outgrpc` | server function | `host:port,workers` | Calls a queue. `workers` is accepted so the flag matches the queue, but the visit sends one RPC from the handler thread. |

The course comment wrote `--ingrpc 0.0.0.0,5` with no port. A listen address needs one, so the form here is `interface:port,workers`. IPv4 only.

`workers` on `--ingrpc` is the server thread pool. `workers` on a queue's `--outgrpc` is how many sender threads pull from that queue's buffer.

## Three services, one job

`job.proto` is the shared `Job` (id, arrival time, wait tasks, and the original CSV `raw_line`). Each direction is its own service with the same `Submit` RPC:

| Proto | Client | Server |
| --- | --- | --- |
| `queue_to_server.proto` | `queue.py --outgrpc` (default) | `server_func.py --ingrpc` |
| `server_to_queue.proto` | `server_func.py --outgrpc` | `queue.py --ingrpc` |
| `queue_to_queue.proto` | `queue.py --outgrpc host:port,workers,queue` | `queue.py --ingrpc` |

A queue listen port registers both queue-ingress services. They admit into the same buffer. `Submit` returns `accepted=false` when the buffer is at `--qsize`; the queue logs `ERROR QUEUE-FULL` and the sender still logs `DEPART-VIA` because the job left the sender. A transport failure is an error line in the sender log and does not log `DEPART-VIA`.

The server function accepts every job. Its `Submit` handler sleeps the waits before returning, so the queue sender stays occupied for the whole visit. That is the backpressure. The server function's `DEPART-VIA` is the edge it picked after the visit (a file or the next queue).

`ARRIVE-VIA` / `DEPART-VIA` use the same Lab 0 line as the file path. The edge label is a filename basename or `host:port` or the `source_edge` the sender put on the request (its queue id).

## Startup and shutdown

Start the listener before the sender, the same order as a FIFO reader before the writer.

A process with only file inputs exits at EOF. A process with `--ingrpc` stays up until Ctrl-C, including when it also read a file, because more RPCs can still arrive. Ctrl-C stops the listen port, then the buffer's stop marker lets the sender threads finish.

## Examples

Queue file in, server function over gRPC, sink file. Start the server function first.

```text
python implementation/obj-a/server-func-stateless/server_func.py --ingrpc 0.0.0.0:50052,2 -o sink.csv --logfile sf.log
python implementation/obj-a/queue/queue.py --qsize 10 -i jobs.csv --outgrpc 127.0.0.1:50052,2 --logfile q.log
```

Server function reads a file, queue listens and writes the sink. The queue keeps running after the server function exits; Ctrl-C stops it.

```text
python implementation/obj-a/queue/queue.py --ingrpc 0.0.0.0:50051,4 -o sink.csv --logfile q.log
python implementation/obj-a/server-func-stateless/server_func.py -i jobs.csv --outgrpc 127.0.0.1:50051,1 --logfile sf.log
```

Queue to queue, then into a server function:

```text
python implementation/obj-a/server-func-stateless/server_func.py --ingrpc 0.0.0.0:50052,2 -o sink.csv
python implementation/obj-a/queue/queue.py --qid q1 --ingrpc 0.0.0.0:50051,4 --outgrpc 127.0.0.1:50052,2
python implementation/obj-a/queue/queue.py --qid q0 -i jobs.csv --outgrpc 127.0.0.1:50051,2,queue
```

There is no TLS. Docker and the C++ ports are separate tasks; they can speak these same protos later.
