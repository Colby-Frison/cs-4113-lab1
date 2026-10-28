# C++ stage server

`match_server` is the C++ port of [server-stages](../server-stages/README.md). One binary, `--stage` selects the visit, and the flags match the Python program. It speaks the Objective A protos in [../../obj-a/proto](../../obj-a/proto): `QueueToServer` on `--ingrpc`, `ServerToQueue` on `--outgrpc`. The log prefix is `sv_pid`, and the message body is the Lab 0 line.

Python remains the reference and the Windows file demo. This binary is what a later image runs when the edge is gRPC. The queue process stays the Objective A Python queue.

Ready check uses the same rule: failure probability `--ready-fail-p`, visit cap `--max-ready-visits`, and an optional `--state-file` of `job_id visits` lines. The random stream is C++ `mt19937`, so a seed does not reproduce the Python `random.Random` sequence. The threshold rule is the same.

## Build

The file-only self-test needs a C++17 compiler. `match_server` also needs protobuf and gRPC C++ (CONFIG-mode CMake packages) and `gRPC::grpc_cpp_plugin`.

```text
cmake -S implementation/obj-b.matchmaking/cpp -B implementation/obj-b.matchmaking/cpp/build
cmake --build implementation/obj-b.matchmaking/cpp/build
```

On a machine with vcpkg:

```text
cmake -S implementation/obj-b.matchmaking/cpp -B implementation/obj-b.matchmaking/cpp/build -DCMAKE_TOOLCHAIN_FILE=%VCPKG_ROOT%/scripts/buildsystems/vcpkg.cmake
```

A Debian-style install is `protobuf-compiler-grpc`, `libgrpc++-dev`, and `libprotobuf-dev`. If those packages are missing, CMake still configures `match_file_selftest` and warns that `match_server` was skipped.

`match_file_selftest` reads one CSV row, sleeps task 3 into a sink file, and checks that formatting the row and parsing it again keeps the job id and the waits. It does not open a socket.

## Flags

```text
match_server
  --stage validate|rating|route|search|ready|allocate
  (-i INFILE | --ingrpc interface:port,workers)
  [-o OUTFILE | --outgrpc host:port,workers]
  [--route-edge region=path | --route-edge region=host:port,workers]
  [--pass DEST --retry DEST --fail DEST]
  [--ready-fail-p P]
  [--max-ready-visits N]
  [--state-file PATH]
  [--seed N]
  [--qid q-search]
  [--server-id 0]
  [--logfile PATH]
```

`workers` on `--ingrpc` is the gRPC server pool, via the runtime thread quota. A file input exits at EOF. A listen port stays up until Ctrl-C. A destination that contains a comma is `host:port,workers`. Any other destination is a file, opened in append mode before the first job.
