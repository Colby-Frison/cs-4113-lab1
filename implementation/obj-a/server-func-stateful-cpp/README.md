# Stateful K-V function in C++

This ports the basic stateful function: an in-memory `job_id → visit count` map,
protected by a mutex. Each visit increments its count, runs the wait tasks, and
forwards the original job to one file or gRPC output. Counts reset on restart
and are logged as `state-visits` at EOF or graceful shutdown.

## Build

Requires CMake, a C++17 compiler, Protobuf, and gRPC C++ with `grpc_cpp_plugin`.
From the repository root:

```bash
cmake -S implementation/obj-a/server-func-stateful-cpp -B implementation/obj-a/server-func-stateful-cpp/build -DCMAKE_BUILD_TYPE=Release
cmake --build implementation/obj-a/server-func-stateful-cpp/build --config Release -j 2
```

This builds only `stateful_server`. Multi-configuration generators place it in
`build/Release/`. Use `--help` for flags.

## Run

Start the function:

```bash
implementation/obj-a/server-func-stateful-cpp/build/stateful_server \
  --ingrpc 127.0.0.1:27000,2 -o sink.csv --logfile sf.log
```

After building the [C++ queue](../queue-cpp/README.md), send an existing workload
from another terminal:

```bash
implementation/obj-a/queue-cpp/build/queue_server \
  -i jobs.csv --outgrpc 127.0.0.1:27000,2 --qsize 1000 --logfile queue.log
```

Use Ctrl-C to stop the function and log its final counts. File input is also
supported with `-i jobs.csv` instead of `--ingrpc`.

## Tests

With both binaries built and Objective A's Python requirements installed:

```bash
python implementation/obj-a/server-func-stateful-cpp/tests/test_integration.py -v
```

Two tests cover visit counting/reset and concurrent C++/Python queue flow.
There is no persistence or replication in this port; those remain Python stretch features.
