# Large-model mock-up

A laptop-sized stand-in for the Lab 0 LARGE study: Internet packet forwarding across Autonomous Systems. That study used 6,474 queues. This script uses three queues and both server functions so every Objective A program and every edge type actually runs.

The specialized hop-countdown server function is not part of Objective A. Each server function here sleeps the job's `W` and forwards the original CSV row. The shape of the path is the large model. The work inside the visit is the wait-task demo.

## How a job moves

```text
generator --file--> q0 stub
    --gRPC queue-to-server--> stateless forward SF
        --gRPC server-to-queue--> q1 transit
            --gRPC queue-to-queue--> q2 neighbor
                --file--> stateful SF --file--> sink.csv
```

`q0` is the stub that receives the generator file, the same role as a source AS. The stateless function is one forwarding visit. `q1` to `q2` is an inter-AS link with no server in between, which is why that hop uses the queue-to-queue service. The stateful function is the visit that remembers how many times each job id arrived. It runs after `q2` has finished writing, because a regular file reader stops at EOF and a listen port would stay up until Ctrl-C.

Buffers are `--qsize 8`, the Lab 0 floor of `max(8, 4 × servers)` for a one-server AS. Arrivals are immediate (`fixed 0`) and each task sleeps a short uniform draw, so six jobs finish in a few seconds. The LARGE study itself used a Poisson arrival and a 4-step service. Poisson waits are whole seconds, which makes a live mock-up slow. Swap the generator flags in `run.ps1` when you want that distribution.

## Run

From any directory, in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File "implementation/obj-a/mockups/large-model/run.ps1"
```

gRPC must be installed (`pip install -r implementation/obj-a/requirements.txt`). The script listens on `127.0.0.1` ports `50151`, `50152`, and `50153`. It writes `run/jobs.csv`, `run/forwarded.csv`, `run/sink.csv`, and one log per process. `run/` is gitignored.

Listeners start first. After the sink has one row per generated job, the script stops the listen ports and prints how many of each Lab 0 event it saw. `state-visits` comes from the stateful log.

The script then runs [analyze_run.py](../analyze_run.py). That builds `run/analysis/events.log`, `qmet.csv`, and `qconf.csv` from the process logs and runs the Objective A filters, USE, system metrics, job metrics, event trace, and pretty-printer. Time in those files is milliseconds since the first event. `q1` is a buffer with no server, so it has queue samples and no visit row.

The same three AS processes connected by named pipes, with no addresses, are in [the FIFO mock-up](../large-model-fifo/README.md).
