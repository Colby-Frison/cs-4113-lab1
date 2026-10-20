# Stateful server function

Same wait-and-forward visit as `../server-func-stateless/`. This process also counts how many times each job id is seen.

The count stays in memory. The outgoing edge still receives the original job line, not a dump of the dictionary. When the input reaches EOF, the process logs one operational line per job:

```text
state-visits j0 1
```

That line is not a Lab 0 event. The Lab 0 events are the same list as the stateless server.

## Flags

```bash
python server_func.py
  -i INFILE
  [-o OUTFILE ...]
  [--qid q0]
  [--server-id 0]
  [--logfile PATH]
```

## Example

```bash
python server_func.py -i queued.csv -o sink.csv --logfile sf.log --qid q0
```
