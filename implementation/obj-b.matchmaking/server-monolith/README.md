# Monolithic matchmaking server

One process performs the whole ticket visit. Tasks $0$ through $5$ are validate, rating, route, search, ready check, and allocate. The visit logs `ENTERS-SERVER`, a `TASK-START` / `TASK-END` pair per task, and `EXITS-SERVER`, then appends the original CSV row to one edge.

A row whose job id is not a party ticket, or that is missing one of those six tasks, is logged as an error and skipped. That line is not a Lab 0 event.

The queue in front of this process is the Objective A queue. [topology/monolith.json](../topology/monolith.json) names it `q-match` with `qsize` $32$. `workers` $4$ is the gRPC pool for a later launch. The file path is one thread.

```text
python server_func.py
  (-i INFILE | --ingrpc interface:port,workers)
  [-o OUTFILE ...]
  [--outgrpc host:port,workers ...]
  [--qid q-match]
  [--server-id 0]
  [--logfile PATH]
```

File demo, from the repository root, after a generator run:

```text
python implementation/obj-a/queue/queue.py --qsize 32 --qid q-match -i tickets.csv -o queued.csv --logfile q.log
python implementation/obj-b.matchmaking/server-monolith/server_func.py -i queued.csv -o matched.csv --qid q-match --logfile s.log
```

`run_demo.py` runs this shape and the staged shape. The staged server is [server-stages](../server-stages/README.md).
