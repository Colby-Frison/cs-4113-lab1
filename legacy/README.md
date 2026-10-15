# Legacy Lab 0 reference

Trimmed snapshot of [Colby-Frison/lab0](https://github.com/Colby-Frison/lab0) for Lab 1 design and reuse.

## Kept

| Path | Why |
|------|-----|
| `lab0/README.md` | Lab 0 SoW and team roster |
| `lab0/src/analysis/multi-LARGE/` | AS-733 packet-forwarding study (model, graph, results, driver) |
| `lab0/src/analysis/multi-SMALL/analysis_tools.py` | Imported by `large_tools.py` |
| `lab0/src/implementation/obj-b/simple-queue-sim/` | OO discrete-time core: Job, QueueSystem, ServerFunction, EventLog, metrics, config I/O — reusable concepts for Lab 1 processes |
| `lab0/src/implementation/obj-e/multi-queue-sim/` | Multi-queue entry, scripts, `make-queue-inputs` / queue-studio tooling |
| `lab0/src/implementation/obj-e/create-config-network/` | Graph → queue config helper used by `as_model.py` |
| `lab0/src/implementation/obj-e/pretty-print-events/` | Job-trace pretty printer |
| `lab0/src/implementation/obj-a/workload-generator/` | Packet / workload stream generator |
| `lab0/src/implementation/obj-c/` | Analysis CLIs (USE, filters, plots, traces) |

## Still removed

Design notes, hand-written examples trees, ED `multi-SMALL` study (except `analysis_tools.py`), Obj D, CI/test runners, VS Code metadata, nested `.git`, and regenerated `workloads/` / large `runs/` outputs.

## Reuse notes for Lab 1 (simulator OO → live backend)

Lab 1 is process-based, not a single discrete-time binary, but these types map cleanly:

| Lab 0 type / file | Lab 1 reuse idea |
|-------------------|------------------|
| `Job` (`job.hpp`) | Packet job line format, task list / remaining hops |
| `QueueSystem` (`queue_system.hpp`) | Bounded buffer, admit/drop (`QUEUE-FULL`), server slots |
| `ServerFunction` / Sub-X plans | Forward SF: decrement hop work, choose next edge |
| `EventLog` event strings | Same ARRIVE / DEPART / ENTERS-SERVER / … vocabulary via `logger.info()` |
| `config_io` rows | Orchestration config → spawn queues/SFs and wire FIFOs/gRPC |
| `metrics_writer` / qmet fields | Async metric samples or log-bridge to Lab 0 plot tools |
| `create-config-network` | Build AS subgraph configs for SMALL→LARGE |

Prefer **porting ideas and formats** into Lab 1 Python (or later C++ gRPC) rather than linking the discrete-time tick loop unchanged.

Lab 1 application design: [documentation/local_notes/app-as-packet-forwarding.md](../documentation/local_notes/app-as-packet-forwarding.md).
