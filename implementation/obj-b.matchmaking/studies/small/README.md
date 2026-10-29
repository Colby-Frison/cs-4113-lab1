# Small matchmaking study

A queue in this study is one stage of the ticket pipeline. A bracket (mode, region, and skill band) is how a later Medium or Large study grows the queue count. Six stages sit in the Small band of 2–10 queues. This pass does not add empty bracket copies to reach 10. The sink row is the original ticket, so the study does not report MMR spread.

The runs live here, one directory per server set. That is the same layout the mapping note describes for `analysis/obj-b.XX/small/`, kept inside the matchmaking tree.

## Metrics

The quantity to optimize is time-to-match together with drops. A job is one party ticket.

| Metric | Where to read it |
| --- | --- |
| Time-to-match | First `ARRIVE-VIA` on `q0` through the sink `DEPART-VIA` (`matched.csv` or `failed.csv`). `latency.png` is that span. |
| Utilization | Share of the stage's worker pool inside a visit (`TASK-START` to `TASK-END`). |
| Saturation | Jobs waiting in that stage's buffer. |
| `QUEUE-FULL` | A ticket the buffer refused. It never continues, and it is not a ready-check result. `errors.png` counts only these. |
| Ready-check failure | The ready stage rolled a failure on a visit that had already reached the cap, and wrote the ticket to `failed.csv`. |
| Throughput | Tickets per millisecond that reach a sink. |

`summary.md` keeps `QUEUE-FULL` and ready-check failures in separate columns.

## Queue ids

Objective A `compute-use` reads numeric queue ids. The event id and the dial address are separate. Host names are `127.0.0.1` ports.

| Log id | Stage | Task | Workers in this study |
| --- | --- | --- | --- |
| `q0` | admit | validate | 1 |
| `q1` | rating | rating | 1 |
| `q2` | route | route | 1 |
| `q3` | search | search | the pool under test |
| `q4` | ready | ready check | 1 |
| `q5` | allocate | allocate | the same pool as search |

Route sends `na` to `q3`. Ready passes to `q5`, retries to `q3`, and writes `failed.csv` once the visit cap is reached. Allocate writes `matched.csv`.

## Configuration

How the runner starts processes, where a ticket goes, and how to watch a run is in the [study runner guide](../README.md). The commands for this size are:

`run_small.py` runs one scenario or all seven:

```text
python implementation/obj-b.matchmaking/studies/small/run_small.py
python implementation/obj-b.matchmaking/studies/small/run_small.py --only base
```

Each scenario starts twelve local processes: `implementation/obj-a/queue/queue.py` and `implementation/obj-b.matchmaking/server-stages/server_func.py`. Listeners bind before the generator starts. gRPC links every hop after admit. Windows has no FIFOs, and a file edge between stages would close on EOF and break the ready-check retry.

The generator writes `runs/<scenario>/jobs.csv`. `q0` reads a named pipe that carries the same bytes. A regular file returns EOF as soon as the reader catches the writer, which would admit the whole file as a burst and throw away the interarrival. The pipe is only the admit edge.

`topology/baseline.json` stays the product topology (search and allocate at 4 workers, search depth 32, interarrival 14 s). The runner overrides workers, depth, count, arrival mean, and time scale.

The Small runs use the Python stage server. `cpp/build/match_file_selftest` is the local C++ proof that one stage visit still parses and sleeps a ticket. `match_server` links only where the C++ gRPC packages exist.

The Objective A submit path keeps one gRPC channel open per peer. A new TCP connection per ticket exhausts Windows ephemeral ports (`WSAEADDRINUSE`) before the second scenario finishes.

## Workload

Default count is 1,000 tickets, the low end of 1,000–10,000. `--count` can raise it. Region is `na`, mode is `ranked`, seed is 7. `--time-scale 0.02` multiplies every draw, including the interarrival, so seven sets finish locally.

Unscaled search service has mean $11.45$ s once the band cuts and the party mix are included. A ready failure below the cap of 2 sends the ticket through search again, so the expected search visits are $1 + 0.2 = 1.20$. One search server and the product interarrival of $14$ s sit near $\rho \approx 0.8$. That arrival would leave a pool of 10 idle, and adding workers would not move the search line. The study interarrival is

$$
\frac{11.45 \times 1.20}{0.8 \times 10} = 1.718 \text{ s}
$$

so the baseline pool, including retries, is the load near $\rho = 0.8$. The same arrival puts the single ready worker near $\rho = 1.40$, because ready is a fixed $2$ s and it sees the same $1.20$ visits. Admit, rating, route, and ready stay at 1 worker: a pool of 1 has nothing to shrink by 10%.

Every stage buffer holds the ticket count. Search is sized that way so a slower pool queues. The other stages use the same depth so the ready line is waiting time, and `matched.csv` plus `failed.csv` can name every ticket.

## How to read the figures

Six figures per set are in `results/<scenario>/`. The cross-set comparison is `results/sensitivity-servers.png` (latency, `QUEUE-FULL`, ready-check failures, and throughput) and `results/sensitivity-latency.png`. Time is milliseconds from the first event. The dark line on the USE panels is `q3` search. Accessory tool output, including `q3-qmet.csv` from `filter-by-queue`, is in `results/<scenario>/tools/`. `etrace.png` is the first 40 jobs. `longest-jobs.png` is the three longest end-to-end tickets.

- Utilization is the fraction of that stage's pool inside a visit. On the baseline, search spends the arrival window oscillating up against $1$ and settles near $0.7$ if the busy windows are averaged. A larger pool pulls that trace down. Ready stays near $0.9$ in every set, which is one worker occupied for almost the whole run.
- Saturation is jobs waiting. Search's waiting line is the series the server sweep moves. Ready's waiting line climbs into the hundreds in every set and then drains after arrivals stop.
- `errors.png` is `QUEUE-FULL` only, drawn from zero. It is flat at zero here.
- Throughput is sink departures per millisecond. It follows the ready worker: one visit is $0.040$ s after the time scale, and retries share that worker.
- Latency is time-to-match. The scaled search sleep is a fraction of a second. A mean near $15$–$24$ s is the ready backlog.

## What the seven sets did

The resource is the worker pool on search and on allocate, both set to the same integer. The other four stages stay at 1. The seven pools are $10$, $9$, $8$, $7$, $12$, $15$, and $20$ (`floor` of $-10\%$, $-20\%$, $-30\%$, $+25\%$, $+50\%$, $+100\%$ from a baseline of $10$).

| Scenario | Workers | Matched | Ready-check failures | `QUEUE-FULL` | Mean latency (ms) | Search peak saturation |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `base` | 10 | 957 | 43 | 0 | 15159 | 12.8 |
| `srv-10` | 9 | 948 | 52 | 0 | 14320 | 15.6 |
| `srv-20` | 8 | 949 | 51 | 0 | 24328 | 20.5 |
| `srv-30` | 7 | 951 | 49 | 0 | 21071 | 88.3 |
| `srv+25` | 12 | 955 | 45 | 0 | 20056 | 3.0 |
| `srv+50` | 15 | 948 | 52 | 0 | 21802 | 2.0 |
| `srv+100` | 20 | 952 | 48 | 0 | 19035 | 1.0 |

Every ticket reached a sink. `QUEUE-FULL` is 0 in all seven. Ready-check failures are $43$ to $52$ tickets, around the $4\%$ implied by failing both rolls at probability $0.2$. The count moves a little between sets because the single ready worker applies the random stream in visit order, and a different search pool changes that order.

Search saturation falls as the pool grows: about $88$ tickets waiting at 7 workers, about $13$ at the baseline of 10, and about $1$ at 20. Mean time-to-match stays in one band, about $14$–$24$ s. The run with the longest mean ($24$ s at 8 workers) is also the run with the longest ready line. Extra search workers drain search. They do not drain ready.

## Does this match matchmaking?

Section 5 of the mapping note describes a bracket queue and a matcher that holds an open roster until the match fills. Players in a thin bracket wait because the bracket lacks arrivals, and a matcher can look busy while the roster is incomplete.

This graph answers a narrower question, and the measurements fit that question. Search utilization is time in the drawn wait, so a high value means those workers are inside that sleep. The ready check is the stage a party cannot skip, it is staffed at one worker, and the offered load on it is about $1.4$. The waiting line in front of it grows for the whole arrival window, in every server set, up to about $450$–$580$ tickets. That is the matchmaking fact this Small model can support: capacity on the long sleep stops mattering once a later single-threaded check is past $\rho = 1$.

## Takeaway

Staff the ready check before adding search workers. At 12 search workers the search line is already a handful of tickets, and the ready line is still about 500. The product ready depth of 16 would have turned that line into `QUEUE-FULL` (a player who never finishes the check) instead of a long time-to-match. Keep that drop in a different column from the ready-check failure, which is an application result of about $4$–$5\%$ at the baseline failure probability and visit cap.

A later study that grows brackets, rather than copies of these six stages, is what can speak to MMR spread and to idle brackets beside a saturated one.
