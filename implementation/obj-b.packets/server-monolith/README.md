# Monolithic router

One server function does a router's whole job in one visit. The router is one queue and this one process. The visit logic is `Router.monolith` in [pk/router.py](../pk/router.py), and [the stages](../server-stages/README.md) split the same logic into three visits.

Back to the [application README](../README.md).

## One visit

1. Log `ENTERS-SERVER`. Sleep tasks 0, 1, and 2 in order, each between `TASK-START` and `TASK-END`. Log `EXITS-SERVER`.
2. **Validate.** A TTL of 0 or less logs `ERROR TTL-EXPIRED` and drops the packet.
3. **Look up** the destination, by longest-prefix match. No match logs `ERROR NO-OUT-EDGE` and drops the packet.
4. If the route is `local`, write the original row to the sink and log `DEPART-VIA sink-q17.csv`. The packet is delivered.
5. Otherwise reduce the TTL by one. If that reaches 0, log `ERROR TTL-EXPIRED` and drop the packet.
6. Call `Submit` on the next router's queue with the new TTL, and log `DEPART-VIA host:port`.

`Submit` returns to this router's queue only after step 6, so a queue sender thread stays busy for the whole visit. That is what keeps waiting packets in the buffer. A next hop with no entry in `addresses.csv` logs an error and `ERROR NO-OUT-EDGE`. If the next queue is full, that queue logs `ERROR QUEUE-FULL`, and this router still logs `DEPART-VIA`, because the packet left. If the RPC itself fails, an error line is logged with no `DEPART-VIA`, and the packet is not retried.

The whole visit is slept before any decision, so a packet that will be dropped still costs the full wait. That is the main behavioral difference from the stages.

## Flags

```text
python server_func.py
  --qid q17
  --ingrpc interface:port,workers    workers is the router's server count
  --addresses addresses.csv
  --routes routes/q17.tsv
  --sink sink-q17.csv
  [--server-id 0]
  [--logfile PATH]
```

The process runs until SIGINT or SIGTERM. `workers` must match the workers on the queue's `--outgrpc`.
