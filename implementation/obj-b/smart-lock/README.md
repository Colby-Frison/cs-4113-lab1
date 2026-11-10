
# Lab 1 Objective B - HomeKey Firmware Analysis

This is our Objective B application for Lab 1. We're using HomeKey firmware as the source for a security analysis application that runs through a queue system (at least that's what I picked).

The goal is to check firmware source code for possible security issues while also measuring how the queues perform. The application splits the analysis into smaller jobs so multiple files and checks can be processed in parallel.

We also reuse our Objective A tools to generate graphs and calculate queue metrics.

## Setup

The application is located in `implementation/obj-b/smart-lock/`, and the Objective A tools are in `implementation/obj-a/`.

The HomeKey source is already included in our project setup, so there's no need to clone another repository.

From the smart-lock directory:

```bash
cd implementation/obj-b/smart-lock
```

Install the Python dependencies:

```bash
python3 -m pip install -r requirements.txt
```

The application can run without an ESP32 connected. Hardware is only needed for physical verification.

## Running the Application

The easiest way to run everything is through `run_all.py`.

```bash
python3 run_all.py --source-root path/to/homekey --out runs/baseline
```

Replace `path/to/homekey` with the location of the included HomeKey source.

This generates the workload, runs the queue application, saves the results, and creates graphs using our Objective A tools.

The default configuration uses:

- 6 queues
- 4 workers
- Decomposed architecture
- Python scanning functions
- Poisson arrival distribution with lambda 2

Each run should use a new output directory so we can keep the results from different runs.

## How the Application Works

The workload generator goes through the HomeKey source files and creates jobs for different security checks.

The checks look for:

- **Input bounds:** Potentially unsafe functions like `strcpy`, `strcat`, `sprintf`, and `gets`.
- **Authorization:** Unlock-related code that doesn't appear to have nearby authorization checks.
- **Replay:** Unlock-related code without nearby identifiers for things like nonces, counters, or timestamps.

These checks are meant to identify possible security issues. They don't automatically prove that something is vulnerable.

Each job contains information about the source file, the check being performed, and the file's SHA-256 hash.

The generator keeps the original Lab 0 workload format:

```text
Job Id,arrival time,Tasks
```

Tasks also use the original `tid: W N` format. The W values represent estimated work, but the application actually executes the scanning functions instead of just decrementing simulated work.

## Queue Organization

We have two different architectures.

### Decomposed

The decomposed version splits the application into five stages:

```text
Prepare
   |
   v
Scan (parallel queues)
   |
   v
Review
   |
   v
Verify
   |
   v
Report
```

- **Prepare:** Loads the source file and removes comments and string literals before analysis.
- **Scan:** Searches for patterns related to the selected security check.
- **Review:** Determines whether the scan found any candidates.
- **Verify:** Checks whether a candidate has a supported physical test.
- **Report:** Records the results and finishes the job.

With 6 queues, we have one Prepare queue, two Scan queues, one Review queue, one Verify queue, and one Report queue.

If we increase the total queue count, the additional queues are used for scanning.

The Scan queues can process independent jobs in parallel. Physical verification is kept separate since we only have one hardware target.

### Monolithic

The monolithic version combines Prepare, Scan, and Review into one function.

The remaining stage handles verification and reporting.

This gives us a way to compare several smaller processing stages against a setup with fewer queue transfers.

## Main Files

| File | Purpose |
|---|---|
| `run_all.py` | Runs the workload generator, application, and graph scripts |
| `main.py` | Starts the queue application |
| `simulator.py` | Coordinates job execution and movement between queues |
| `queue_system.py` | Handles waiting jobs and server slots |
| `server_function.py` | Contains the functions used by the different stages |
| `scanner.py` | Contains the firmware security checks |
| `hardware.py` | Handles serial communication with the ESP32 |
| `config_io.py` | Reads queue configurations |
| `workload_io.py` | Loads the workload and writes completed jobs |
| `event_log.py` | Records events during execution |
| `metrics_writer.py` | Writes queue performance metrics |
| `experiments.py` | Runs the different scaling and sensitivity experiments |
| `rpc_client.py` | Communicates with the C++ gRPC backend |
| `workload-generator/workload_generator.py` | Generates firmware analysis jobs |
| `workload-generator/lab0_workload_generator.py` | Reuses the original Lab 0 workload generation functions |

The `cpp/` directory contains the C++ implementation of the application functions. The `proto/` directory contains the gRPC definitions.

The `scripts/` directory contains the build and analysis scripts.

## Running the Steps Separately

We can also generate the workload and run the application separately.

Generate a workload:

```bash
python3 workload-generator/workload_generator.py \
  --source-root path/to/homekey \
  --out-dir runs/manual/inputs \
  --queues 6 \
  --mode decomposed \
  --interval 0:9999 \
  --dist poisson \
  --lam 2 \
  --seed 5113
```

Run the queue application:

```bash
python3 main.py \
  --config runs/manual/inputs/qconf.csv \
  --out runs/manual/outputs \
  --workers 4
```

Generate the graphs:

```bash
python3 scripts/analyze_all.py \
  --results runs/manual/outputs
```

The graphing script uses our existing Objective A tools, so we don't need a separate plotting implementation.

## Outputs

Each run creates an `inputs/` directory and an `outputs/` directory.

### Inputs

| File | Description |
|---|---|
| `workload.csv` | Generated workload |
| `inN.wl` | Workloads for the different input queues |
| `jobs.jsonl` | Job information including source paths, hashes, and checks |
| `qconf.csv` | Queue configuration |
| `manifest.json` | Information about the generated workload |
| `corpus/` | Copies of the source files used for analysis |

### Outputs

| File | Description |
|---|---|
| `qmet.csv` | Queue performance measurements |
| `qconf.csv` | Configuration used during execution |
| `events.log` | Events recorded during job execution |
| `events-longest.log` | Events for the longest completed jobs |
| `out.wl` | Completed jobs |
| `findings.jsonl` | Source analysis and verification results |
| `dropped.jsonl` | Jobs dropped during execution |
| `summary.json` | Overall run summary |
| `metrics-info.json` | Timing and measurement settings |

When graphing is enabled, the Objective A scripts also generate files like `quse.csv`, `sysmetrics.csv`, and `emetrics.csv`.

The graphs include:

- Queue utilization
- System metrics
- Waiting time histogram
- Service time histogram
- Arrival latency
- Longest job trace

These let us compare how the different queues behave and identify possible bottlenecks.

## C++ and gRPC

The application also supports a C++ backend using gRPC.

The Python application still manages the queues and workers, but the scanning functions can execute in C++.

On Ubuntu, install the required packages:

```bash
sudo apt-get install build-essential cmake pkg-config \
  libgrpc++-dev libprotobuf-dev protobuf-compiler \
  protobuf-compiler-grpc
```

Build the C++ server:

```bash
bash scripts/build_cpp.sh
```

Run the application using C++:

```bash
python3 run_all.py \
  --source-root path/to/homekey \
  --out runs/cpp-baseline \
  --engine cpp-grpc
```

This lets us compare the application using Python functions against the C++ implementation with gRPC communication.

The queues still run on the same host. They aren't separate physical machines.

## Experiments

The experiments script runs multiple queue configurations and collects performance results.

```bash
python3 experiments.py \
  --source-root path/to/homekey \
  --out runs/full-suite
```

We test four queue sizes:

| Scale | Queues |
|---|---:|
| Small | 6 |
| Medium | 16 |
| Large | 128 |
| Very large | 1024 |

Each scale includes seven experiment settings:

1. Baseline
2. Slower arrivals
3. Burst arrivals
4. One worker
5. Eight workers
6. Two logical server slots per compute queue
7. Monolithic architecture

These experiments help us see how arrival rates, worker counts, server slots, and queue organization affect performance.

To run only the small scale:

```bash
python3 experiments.py \
  --source-root path/to/homekey \
  --out runs/small-suite \
  --scales small
```

We can also skip graph generation:

```bash
python3 experiments.py \
  --source-root path/to/homekey \
  --out runs/no-plots \
  --no-plots
```

Then generate the graphs afterward:

```bash
python3 scripts/analyze_suite.py \
  --suite runs/no-plots
```

The experiment results are saved separately so we can compare the configurations.

## Physical ESP32 Testing

The application supports physical verification through an ESP32, but this is optional.

The source analysis works without hardware connected. When a possible issue is found, the Verify stage checks whether we have a matching test plan.

If a test plan exists and the ESP32 is connected, the application can send commands over serial and check the response.

For example:

```bash
python3 run_all.py \
  --source-root path/to/homekey \
  --out runs/physical \
  --test-catalog my_target_tests.json \
  --serial-port /dev/ttyUSB0
```

The test catalog identifies the firmware build and specifies the commands needed to test particular findings.

A physical test needs a known starting state, such as the lock being locked before testing. The result should come from the actual firmware response rather than just lighting an LED when a finding is detected.

Findings without a supported test plan are marked as needing a target-specific test. If a plan exists but no hardware is connected, the finding is marked as awaiting hardware.

Finding suspicious source code doesn't automatically mean we've demonstrated an unauthorized unlock.

## Metrics

The application collects several performance measurements:

- **Utilization:** How much time the logical server slots spend occupied.
- **Saturation:** How many jobs are waiting in the queue.
- **Errors:** Jobs that fail or are rejected because of queue capacity.
- **Throughput:** How many jobs finish within a period of time.
- **Waiting time:** How long jobs spend waiting before processing.
- **Service time:** How long jobs spend being processed.
- **End-to-end latency:** How long a job takes from entering the system to finishing.

These measurements come from the queue metrics and event logs.

The goal is to improve throughput and reduce waiting time and latency without losing jobs or changing the analysis results.

## Tests

Run the Python unit tests with:

```bash
python3 -m unittest discover -s tests -v
```

For the C++ implementation, we can also run:

```bash
python3 scripts/check_cpp.py
```

The C++ check uses the built server and the gRPC dependencies.

## Additional Documentation

More information about the queue layout, scheduling, metrics, experiment design, and limitations is available in `docs/architecture.md`.

The application is meant to demonstrate a queue-based firmware analysis workflow and measure its performance. Security findings are candidates for further review, not automatic proof of vulnerabilities.
