# C++ queue (gRPC)

`queue_server` is the C++ port of [../queue/queue.py](../queue/queue.py). Same flags, Obj A protos, Lab 0 events (`Q_pid`), drop-on-full. Shared CSV/event helpers live in [../cpp](../cpp) so the KV / stateless SF ports can reuse them.

Python `queue.py` remains the study reference.

## Layout

| Piece | Role |
| --- | --- |
| `../cpp/job.*`, `../cpp/events.*` | Shared parse + logging |
| `src/buffer.*` | Bounded queue (this port only) |
| `src/grpc_edge.*` | Listen/dial for the three queue protos |
| `src/main.cpp` | CLI + threads |
| `src/file_selftest.cpp` | No-gRPC smoke test |

## Build

```text
cmake -S implementation/obj-a/queue-cpp -B implementation/obj-a/queue-cpp/build
cmake --build implementation/obj-a/queue-cpp/build
```

`queue_file_selftest` always builds. `queue_server` needs protobuf + gRPC C++ (CONFIG packages) and `gRPC::grpc_cpp_plugin`. Without them CMake warns and skips that target — same pattern as matchmaking's C++ port.

MSYS2: `mingw-w64-ucrt-x86_64-protobuf`, `mingw-w64-ucrt-x86_64-grpc`, then `-DCMAKE_PREFIX_PATH=C:/msys64/ucrt64`. Debian: `protobuf-compiler-grpc`, `libgrpc++-dev`, `libprotobuf-dev`.

```text
queue_file_selftest <jobs.csv> <sink>
```

## Flags

```text
queue_server
  [-i INFILE ...] [-o OUTFILE ...]
  [--ingrpc interface:port,workers ...]
  [--outgrpc host:port,workers[,server|queue] ...]
  [--qsize N] [--qid q0] [--logfile PATH]
```

## gRPC interop with Python SF

```text
python implementation/obj-a/server-func-stateless/server_func.py --ingrpc 127.0.0.1:50052,2 -o sink.csv --logfile sf.log
implementation/obj-a/queue-cpp/build/queue_server --qsize 10 -i jobs.csv --outgrpc 127.0.0.1:50052,2 --qid q0 --logfile q-cpp.log
```
