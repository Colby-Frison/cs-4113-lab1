# Objective A framework (first slice)

Wall-clock wait-task jobs, a dropping queue, and server functions that sleep those waits and log Lab 0 events.

| Piece | Path |
| --- | --- |
| Job CSV and event lines | `lib/` |
| Workload generator | `workload-generator/` |
| Queue | `queue/` |
| Stateless server function | `server-func-stateless/` |
| Stateful server function | `server-func-stateful/` |

`example_code/` is unchanged. gRPC flags exit with a "not implemented" message.

Run the tests from the repository root:

```bash
python -m unittest discover -s implementation/obj-a/tests -v
```

File-based run (works in PowerShell). Start the server function after the queue has written its output, or use FIFOs and the order in the queue README.

```text
python implementation/obj-a/workload-generator/workload_generator.py --arrival-dist fixed --arrival-fixed 0 --wait-dist fixed --wait-fixed 0.05 --count 5 --out jobs.csv
python implementation/obj-a/queue/queue.py --qsize 10 -i jobs.csv -o queued.csv --logfile q.log
python implementation/obj-a/server-func-stateless/server_func.py -i queued.csv -o sink.csv --logfile sf.log
```
