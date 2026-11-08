# Large-model mock-up, FIFO edges

Same three-AS stand-in as [the gRPC mock-up](../large-model/README.md), with a named pipe on every live hop. A pipe is a path. Nothing in this script binds an address.

```text
generator --file--> q0 stub
    --fifo--> stateless forward SF
        --fifo--> q1 transit
            --fifo--> q2 neighbor
                --fifo--> stateful SF --file--> sink.csv
```

On a file edge, queue-to-server, server-to-queue, and queue-to-queue are the same thing: the sender appends the CSV row and the next process reads it. The gRPC mock-up needs three services to tell those hops apart. This one does not.

Each pipe blocks in `open` until the other end is open, which is why the script starts the sink end before `q0`. A queue opens its outfile before it reads, so it waits for the next reader instead of filling `--qsize` first. A server function opens its infile first, then its outfile. Once every process has been started, those opens meet and the jobs flow. When `q0` finishes `jobs.csv` it closes its pipe. Each reader then sees EOF and exits, including the stateful function, which is in the live path here. The gRPC version kept listen ports up, so that function ran only after the file was complete.

Buffers, job count, and the short uniform waits match the gRPC mock-up.

## Run

`mkfifo` is a Linux call. PowerShell and Git Bash do not provide the pipes these programs open. From the repository root in WSL or another Linux shell:

```bash
bash implementation/obj-a/mockups/large-model-fifo/run.sh
```

The script creates the pipes under `/tmp` (a Linux filesystem). Logs, `jobs.csv`, and `sink.csv` go to `run/`, which is gitignored. The file-only path does not import grpcio.

After the pipeline exits, the script runs [analyze_run.py](../analyze_run.py) when `pandas`, `numpy`, and `matplotlib` import. The plots and tables land in `run/analysis/`. Time there is milliseconds since the first event. `q1` is a buffer with no server, so the visit metrics cover `q0` and `q2` only.
