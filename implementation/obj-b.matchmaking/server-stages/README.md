# Stage servers

One program, six stages. `--stage` selects the task that is slept. The forwarded row is still `raw_line`, so the next queue sees the same six waits and only its own stage sleeps one of them.

| Stage | tid | Edge |
| --- | --- | --- |
| validate, rating, search, allocate | 0, 1, 3, 5 | exactly one `--outfile` or `--outgrpc` |
| route | 2 | `--route-edge region=path` or `region=host:port,workers` |
| ready | 4 | `--pass`, `--retry`, and `--fail` |

Route reads the region token in the job id. A region with no edge finishes the visit and logs `ERROR NO-OUT-EDGE`.

Ready check draws a failure with probability `--ready-fail-p` (baseline $0.2$). The visit count for that job id starts at $1$ on the first entry. A failure below `--max-ready-visits` (baseline $2$) uses `--retry`, which the file demo feeds back into the search queue. A failure once the cap is reached uses `--fail`. A pass uses `--pass` on every visit. `--state-file` stores `job_id visits` so the next process, used when the file demo runs search again, still sees the count. The log line `state-visits` is operational. It is not a Lab 0 event.

`workers` on `--ingrpc` is the server pool. Search and allocate are $4$ in the baseline topology so a later reduction has a pool to shrink. This file is one process either way.

```text
python server_func.py
  --stage validate|rating|route|search|ready|allocate
  (-i INFILE | --ingrpc interface:port,workers)
  [-o OUTFILE | --outgrpc host:port,workers]
  [--route-edge region=dest ...]
  [--pass DEST --retry DEST --fail DEST]
  [--ready-fail-p P]
  [--max-ready-visits N]
  [--state-file PATH]
  [--seed N]
  [--qid q-search]
  [--server-id 0]
  [--logfile PATH]
```

A destination with a comma is `host:port,workers` and is dialed as server-to-queue. Any other destination is a file. The file-only path does not import grpc.

The C++ port of this program is [../cpp/README.md](../cpp/README.md). The pipeline it belongs to is [../architecture.md](../architecture.md).
