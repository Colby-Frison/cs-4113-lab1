# Stateful server function

Same wait-and-forward visit as [the stateless server](../server-func-stateless/README.md), including optional `--ingrpc` / `--outgrpc` ([proto/README.md](../proto/README.md)). Read that page for how a visit sleeps and chooses an edge. This page is only the extra state.

Part of the [Objective A framework](../README.md).

## How the visit count works

The script passes a dict into `serve_job`. On `ENTERS-SERVER`, the dict does `count[job_id] = count.get(job_id, 0) + 1`. The stateless script passes no dict, so that step is skipped. When the input is gRPC, several handlers can enter at once, and the increment takes a lock. The file path is one thread, so it does not need the lock.

The count stays in this process. The outgoing edge still receives the original job line, not a dump of the dictionary. After the input finishes, the process logs one operational line per job, sorted by job id:

```text
state-visits j0 1
```

That line is not a Lab 0 event. For a file input it is written at EOF, after the last visit. For `--ingrpc` the visits keep arriving until Ctrl-C, and the lines are written after the listen port stops. The Lab 0 events are the same list as the stateless server.

## Flags

```bash
python server_func.py
  (-i INFILE | --ingrpc interface:port,workers)
  [-o OUTFILE ...]
  [--outgrpc host:port,workers ...]
  [--qid q0]
  [--server-id 0]
  [--logfile PATH]
  [--load-kv-from PATH.json]
  [--store-kv-to PATH.json]
  [--kv-replicas host:port,host:port,...]
  [--kv-2pc-timeout SECONDS]
```

`--load-kv-from` / `--store-kv-to` persist the visit map as a JSON object
(`{"j0": 1, ...}`). `--kv-replicas` turns each increment into a two-phase
commit against those replica processes (see [../kv-store/README.md](../kv-store/README.md)).

## Example

```bash
python server_func.py -i queued.csv -o sink.csv --logfile sf.log --qid q0 \
  --load-kv-from kv.json --store-kv-to kv.json
```
