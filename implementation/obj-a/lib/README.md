# Shared library

The scripts do not install this package. Each one inserts `implementation/obj-a` on `sys.path` and imports `lib`. File-only runs never import `grpc_runtime`, so grpcio is optional until an RPC flag is present.

Back to the [framework README](../README.md).

## How the modules divide

| Module | How it is used |
| --- | --- |
| `job.py` | Builds and parses one CSV row. `format_job_line` uses the csv module so the Tasks JSON is stored with doubled quotes. `parse_job_line` splits those columns, loads the JSON, and checks each `tid: W seconds` token. `iter_jobs` skips blanks and the header. `raw_line` is the exact row a server function forwards. |
| `events.py` | Builds the Lab 0 message body only. Callers pass it to `logger.info`. The time token is `time.time()` at the call, formatted with three decimal places. `edge_label` reduces a filesystem path to its basename. A `host:port` has no slash, so it stays whole. |
| `logsetup.py` | One `basicConfig` for the process. `--logfile` selects a file; otherwise logs go to stderr. The prefix is `asctime - Q_pid@host` or `sv_pid@host`, then the level, then the message. |
| `server.py` | `serve_job` is one visit: enter, sleep each task between `TASK-START` and `TASK-END`, exit, then pick one edge uniformly from the files and the gRPC dials. `serve_stream` is the file loop. `run_cli` is what both server-function scripts call. The stateful script passes a dict; the stateless script passes none. |
| `grpc_spec.py` | Parses `--ingrpc` and `--outgrpc` with no grpc import. A missing port fails here, before a socket opens. The dial's service name depends on who parsed it (queue or server function). |
| `grpc_runtime.py` | Loaded only after a flag asked for RPC. A queue listen port registers both `ServerToQueue` and `QueueToQueue` on one buffer. A server-function port registers `QueueToServer` and runs the visit inside `Submit`, so the caller blocks for the sleep. |
| `proto_gen/` | Checked-in stubs. Regenerate with [../proto/generate_stubs.py](../proto/generate_stubs.py) after a `.proto` edit. See [../proto/README.md](../proto/README.md). |

`queue/queue.py` does not import this library's name `queue`. Its directory would shadow the standard library, so the script removes that directory from `sys.path` before `import queue`, then puts `implementation/obj-a` back for `lib`.
