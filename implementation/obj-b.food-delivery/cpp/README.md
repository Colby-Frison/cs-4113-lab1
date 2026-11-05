# C++ server functions over gRPC

This port implements the monolithic, kitchen-only, and courier-only server
functions in C++17. It reuses Objective A's `job.proto`, `queue_to_server.proto`,
and `server_to_queue.proto` without changing their wire contracts. CMake generates
C++ bindings in `build/generated`; generated code and binaries are not committed.

The workload generator, queues, courier registry, orchestration, and analysis
remain Python. `run_grpc.py` uses the same workload/configuration and analysis
as `run_demo.py`. It uses an in-process bounded q0 instead of Objective A's FIFO
queue executable, and adapts the existing dispatcher to invoke C++ couriers.
This is a port of **server functions**, not the entire application.

## Build and run (from repository root)

On macOS with Homebrew:

```bash
brew install cmake grpc protobuf
python3 -m venv implementation/obj-b.food-delivery/.venv
implementation/obj-b.food-delivery/.venv/bin/python -m pip install -r implementation/obj-a/requirements.txt
cmake -S implementation/obj-b.food-delivery/cpp \
  -B implementation/obj-b.food-delivery/cpp/build -DCMAKE_BUILD_TYPE=Release
cmake --build implementation/obj-b.food-delivery/cpp/build -j 4

implementation/obj-b.food-delivery/.venv/bin/python \
  implementation/obj-b.food-delivery/run_grpc.py --mode stages --count 20
implementation/obj-b.food-delivery/.venv/bin/python \
  implementation/obj-b.food-delivery/run_grpc.py --mode monolith --count 20
```

CMake needs installed Protobuf and gRPC CMake packages, including `protoc` and
`grpc_cpp_plugin`. For a custom installation, pass `-DCMAKE_PREFIX_PATH=/your/prefix`.
For other platforms, follow the [official C++ gRPC build instructions](https://grpc.io/docs/languages/cpp/quickstart/).
The server currently uses POSIX `localtime_r` for timestamps (macOS/Linux).

Optional plots:

```bash
implementation/obj-b.food-delivery/.venv/bin/python -m pip install numpy pandas matplotlib
implementation/obj-b.food-delivery/.venv/bin/python \
  implementation/obj-b.food-delivery/run_grpc.py --mode stages --count 30 --plots
```

Integration/regression tests (requires permission to bind loopback TCP ports):

```bash
implementation/obj-b.food-delivery/.venv/bin/python -m unittest discover \
  -s implementation/obj-b.food-delivery/tests -v
```

C++ tests skip when the compiled binary or Python gRPC dependencies are missing.
Build before running them. The normal Python/FIFO command remains available and
does not require gRPC.

## Architecture and responsibilities

```mermaid
flowchart LR
    G[Python live generator] --> Q[Python bounded q0]
    Q -->|QueueToServer.Submit| K[C++ kitchen processes]
    K -->|ServerToQueue.Submit| D[Python q1 and stateful dispatcher]
    D -->|QueueToServer.Submit| C[C++ courier processes]
    C --> S[Completed CSV sinks]
    C -. RPC completion releases reservation .-> D
```

Monolithic mode uses q0 → C++ monolith processes → sinks. Each monolith executes
preparation and delivery in one visit; no dispatcher participates. Every C++
process is **one serial worker**, regardless of gRPC's internal thread count.
The coordinator sends at most one RPC per worker at a time. An additional
concurrent caller receives `RESOURCE_EXHAUSTED`, rather than starting another
visit or silently creating another application queue.

`server.cpp` provides:

- CSV and structured-message validation: IDs use r0 and near/mid/far; tasks
  must be exactly 0 then 1, with finite nonnegative waits and arrival time.
- Preparation (task 0), delivery (task 1), and combined monolithic execution.
- Compact `sv` logs with millisecond Unix timestamps and three-decimal waits.
- Original-row forwarding to a sink or the next queue using gRPC.
- Rejection of conflicting raw/structured fields and repeat job IDs within a
  worker's lifetime. Structured-only requests are normalized to the app's
  three-decimal precision; raw rows from the generator are preserved exactly.

The dispatcher remains the authority for courier reservations. It changes a
courier from available to busy before the RPC and releases it only after a
successful response. The C++ courier writes and flushes the sink and logs its
departure before returning. Failed calls mark the courier failed and fail the
run. `dispatch-state.json` remains an inspection snapshot, not crash recovery.

## Deadlines, failure handling, and measurement limits

All coordinator calls use the remaining run timeout. C++ kitchen calls to q1
use the earlier of the incoming deadline and 30 seconds. Workers poll for
cancellation during waits. No application retry is attempted; a lost response
can leave an uncertain outcome, so the run fails instead of risking duplicate
delivery. The per-worker duplicate set is in memory and is not an exactly-once
or restart-recovery guarantee. See the [gRPC deadline guidance](https://grpc.io/docs/guides/deadlines/).

A successful kitchen response means preparation and handoff finished. If q1
returns `accepted=false`, it has logged `ERROR QUEUE-FULL`; this is a dropped
prepared order, not a delivered order or transport failure. Analysis reconciles
all generated jobs against sinks and queue-drop events.

The runner starts services on OS-selected loopback ports and waits for readiness
before generating arrivals. After generation, it drains q0, waits for all kitchen
RPCs, closes dispatcher admission, drains active couriers, then terminates/reaps
the idle C++ processes. On failure or timeout it cancels channels, stops ingress,
and terminates/reaps processes while preserving diagnostic files. Individual
servers are long-lived processes; standalone use is stopped by the operator.

This runner uses insecure **loopback-only** gRPC for a local lab experiment; it
does not configure remote deployment or authentication. `--listen` on the binary
is available for explicit manual deployment, but the runner always uses localhost.

Unlike the FIFO version, q0's waiting bound has no extra worker-input pipe buffers:
there is at most one in-flight request per worker plus the bounded pending queue.
q1 retains its bounded pending queue, with kitchen RPCs acting as handoffs.
Consequently buffering and overhead differ between Python/FIFO and C++/gRPC;
timing comparisons change both language and transport. They are not evidence of
C++ speedup alone. The services still simulate work with sleeps.

Run artifacts retain `config.json`, `jobs.csv`, queue and worker logs, sink files,
registry snapshots, and the existing analysis outputs. `config.json` also records
`transport: grpc` and `server_language: cpp`. Additional `*.ready` files record
selected ports (historical once the run ends); `dispatch-worker-*.log` files are
Python callback-worker diagnostics, while `courier-*.log` contains the C++ service
events. Errors from each C++ process go to its `*.stderr` file.
