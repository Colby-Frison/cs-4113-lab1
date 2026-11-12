# Shared C++ helpers (Objective A)

Small reusable pieces for the tech-lead C++ ports, shared by the
[queue](../queue-cpp) and [stateful K-V function](../server-func-stateful-cpp).
Future ports should link these instead of copying them.

| File | Role |
| --- | --- |
| `job.hpp` / `job.cpp` | Lab 0 CSV parse and `raw_line` format |
| `events.hpp` / `events.cpp` | Course log prefix + Lab 0 event bodies. Pass `"Q"` or `"sv"` as the component |

No gRPC dependency here. Each port owns its own `grpc_edge` and CMake proto generation.
