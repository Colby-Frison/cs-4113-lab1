# C++ server functions

The preparation and delivery functions are implemented in C++17 using Objective A's
gRPC schemas. Workload generation, queues, courier state, and analysis remain Python.

- `food_server`: one serial worker per process, used by the demo and medium study.
- `food_host`: many independent worker slots per process, used by the large study.
- `common.hpp`: shared job validation and log formatting.

## Build

Install CMake, Protobuf, and gRPC, including `protoc` and `grpc_cpp_plugin`.
On macOS with Homebrew:

```bash
brew install cmake grpc protobuf
```

From the repository root:

```bash
python3 -m venv implementation/obj-b.food-delivery/.venv
source implementation/obj-b.food-delivery/.venv/bin/activate
python -m pip install -r implementation/obj-a/requirements.txt
python -m pip install numpy pandas matplotlib pillow
cmake -S implementation/obj-b.food-delivery/cpp \
  -B implementation/obj-b.food-delivery/cpp/build -DCMAKE_BUILD_TYPE=Release
cmake --build implementation/obj-b.food-delivery/cpp/build -j 2
```

Both executables are created in `build/`, which is ignored by Git.
Build them separately on each machine. For a custom dependency installation,
pass `-DCMAKE_PREFIX_PATH=/your/prefix` to CMake.

## Run and test

With the virtual environment active:

```bash
python implementation/obj-b.food-delivery/run_grpc.py --mode stages --count 20
python implementation/obj-b.food-delivery/run_grpc.py --mode monolith --count 20
python -m unittest discover -s implementation/obj-b.food-delivery/tests -v
```

Add `--plots` to generate Objective A plots. Logs and analysis go into the run's
artifact directory. The dispatcher reserves a courier before service and releases
it after successful output; transport failures or timeouts fail the run.

The large runner uses two shared hosts while preserving separate queue limits and
regional courier pools. See the [large study](../../../analysis/simple/obj-b-food-delivery/large/README.md)
and [VM guide](../../../analysis/simple/obj-b-food-delivery/large/VM.md).
These experiments use simulated waits and local gRPC; timing differences from
Python/FIFO include transport and buffering changes, not just language speed.
