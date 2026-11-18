# C++ router server function

`packet_server` is the C++ port of both [the monolith](../server-monolith/README.md) and [the stages](../server-stages/README.md). One binary, and `--stage` selects the visit. It speaks the Objective A protos in [../../obj-a/proto](../../obj-a/proto) and can replace the Python server function on any router. The queues stay in Python.

Back to the [application README](../README.md).

## Build

`routing_selftest` needs only a C++17 compiler. `packet_server` also needs protobuf, gRPC C++, and `grpc_cpp_plugin`. If they are missing, CMake configures with a warning and skips `packet_server`, so check that the binary exists.

```text
cmake -S implementation/obj-b.packets/cpp -B implementation/obj-b.packets/cpp/build
cmake --build implementation/obj-b.packets/cpp/build -j4
implementation/obj-b.packets/cpp/build/routing_selftest
```

On Debian or Ubuntu, the packages are `libgrpc++-dev`, `libprotobuf-dev`, `protobuf-compiler`, and `protobuf-compiler-grpc`. For an install elsewhere, add `-DCMAKE_PREFIX_PATH=/path/to/install` when configuring. This was built and tested against gRPC 1.51 and protobuf 3.21, talking to Python queues on grpcio 1.85.

## Flags

```text
packet_server
  [--stage monolith|validate|lookup|forward]    default monolith
  --qid q17
  --ingrpc interface:port,workers
  --addresses addresses.csv
  [--routes routes/q17.tsv]    monolith, lookup
  [--sink sink-q17.csv]        monolith, forward
  [--next-qid q18]             validate, lookup
  [--server-id 0]
  [--logfile PATH]
```

## What must stay the same as Python

| Item | Requirement |
| --- | --- |
| Flags | As above, with the same meanings |
| RPC | Serves `QueueToServer`. Calls `ServerToQueue` on the next hop, with `source_edge` set to its own queue id. |
| Return timing | `Submit` returns only after the visit, including the hand-on |
| Input files | `routes/q*.tsv` and `addresses.csv`, in the formats in [architecture.md](../architecture.md) |
| Log lines | The prefix `asctime - sv_pid@host - LEVEL - message`, and the same event body with three decimals on the time |
| Shutdown | Stops on SIGINT or SIGTERM and flushes its logs |

`tests/test_parity_and_network.py` enforces this. It sends the same packets to both servers, covering delivery, forwarding, TTL expiry, no route, and no address at every stage. It compares the log events, the forwarded fields, the sink rows, and the limit on simultaneous visits.

Run it from the repository root, with the built binary:

```text
PACKET_SERVER_BIN=$PWD/implementation/obj-b.packets/cpp/build/packet_server \
  python -m unittest discover -s implementation/obj-b.packets/tests -v
```

## Where it differs from Python

- **Channels.** It opens one channel per next hop and keeps it for the whole run. The Python `submit_job` opens a new channel for every packet.
- **Server limit.** gRPC's sync server sizes its own thread pool, so a counting gate limits visits to `workers`. Python gets the same limit from a thread pool of that size.
- **Memory.** It used 18 MB (3 MB private) per process in a small run, against 41 MB (19 MB private) for the Python server function.
