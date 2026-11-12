# Firewalled packet routing network

Objective B application: the [packet routing network](../obj-b.packets/README.md) with a stateless firewall on every router. Each router checks every packet against an access list before it routes it, and drops packets the list denies.

This is the problem statement and the model, and [architecture.md](architecture.md) is the design. It reuses the packet routing code, its generated topologies and routing tables, its studies, and its Slurm jobs, and adds only the firewall. Everything not described here works as in the packet routing network.

## The problem

A firewall decides, for each packet, whether the network should carry it at all. The simplest kind is stateless: an ordered list of rules, each matching a range of destination addresses, checked from the top until one matches. The matching rule's action, allow or deny, decides the packet. A packet no rule matches is allowed.

Checking is not free. A router compares the packet against the rules one at a time, so the work per packet grows with the length of the list. Unlike a routing lookup, which takes about the same time however large the table is, a long access list slows every packet down.

Given a topology, an access list, and traffic of which some share is unwanted, the study asks:

- How much latency does filtering add, end to end and at each router?
- How much forwarding capacity does unwanted traffic use before it is dropped, and where?
- Does the bottleneck move once every router also filters?
- How much capacity is needed, and what happens when it is cut or added, now that each router does more work per packet?

## Mapping to queues

Everything in the packet routing [mapping](../obj-b.packets/README.md#mapping-to-queues) stays. The firewall adds:

| Real network | Queueing model |
| --- | --- |
| Access list | One shared file of $N$ rules, `address mask action`, checked first match wins. A mask can be a prefix or a wildcard. |
| Blocked servers | Hosts .240 to .255 in every router's `/24`. One wildcard rule denies them, after $N - 1$ rules that no packet matches, so every packet checks all $N$ rules. |
| Checking the list | Extra service time in the router's visit: $N \cdot r$, where $r$ is the time per rule. It is inside the visit, so it occupies a server like the other tasks. |
| Denied packet | Dropped at the router that checks it, and logged `ERROR FIREWALL-DENY` |
| Unwanted traffic | A share $p$ of each generator's packets goes to blocked hosts |
| Where the check runs | The monolith server function, before routing. In the decomposed form, the validate stage, so a denied packet never reaches lookup or forward. |

The access list is the same on every router, and a packet's destination does not change on the way. So a packet the list denies is always dropped at its first router, and a packet it allows passes every later check. That makes the cost easy to predict.

With $W$ the routing work per visit from the packet routing model, the work per visit becomes

$$W_f = W + N r$$

An allowed packet pays $W_f$ at every router on its path. A denied packet pays it once, at its source router. With $v_i$ the visits router $i$ gets per allowed packet, $s_i$ its share of the sources, and $\lambda$ the total offered rate:

$$\lambda_i = \lambda \left[(1 - p)\, v_i + p\, s_i\right] \qquad \rho_i = \frac{\lambda_i\, E[W_f]}{c_i}$$

Filtering raises every router's load by the factor $E[W_f] / E[W]$. Unwanted traffic shifts load toward the source routers: their load includes all of it, and every other router's load drops by the factor $(1 - p)$. Zero-load latency for an allowed packet becomes the number of routers on its path times $E[W_f]$.

In the decomposed form the check adds $N r$ to the validate stage only. A denied packet then costs one validate visit, not a whole router visit. Validate is the shortest stage, so a long list can make it the bottleneck.

The model leaves these out, on top of what the packet routing model leaves out:

- **Stateful filtering.** Connection tracking needs state shared between packets. A stateless list keeps each check independent.
- **Rules on source address, port, or protocol.** These would need new fields in the gRPC `Job` message, and a destination-based list is enough to study the cost of filtering.
- **Faster matching.** Real firewalls often index their rules, which makes the cost grow much more slowly than $N$. A linear list is the worst case, and the simplest to reason about.

## Interpreting the metrics

The Objective A metrics read as in the packet routing [table](../obj-b.packets/README.md#interpreting-the-metrics), with these differences:

| Metric | Reading with a firewall |
| --- | --- |
| Errors | Now two kinds. `ERROR FIREWALL-DENY` is the firewall working as intended, and `ERROR QUEUE-FULL` is still congestion. They have to be counted separately. |
| Per-hop forwarding delay | `time-in-service` now includes the list check, so about $W + N r$ |
| End-to-end latency | Only allowed packets are delivered, so it measures the cost of filtering on wanted traffic |
| Throughput | Delivered packets per second, at most $(1 - p)$ of the offered rate |
| Loss | Overload drops as a share of allowed packets: $(\text{allowed} - \text{delivered}) / \text{allowed}$. Denied packets count separately and are expected. |

Read together:

- **Utilization rises across the network while throughput holds** is the cost of filtering: every router does more work for the same packets.
- **Saturation and drops at source routers only** point to unwanted traffic. Those routers spend capacity on packets that are dropped anyway.
- **Denied share far from $p$** is a configuration fault: the list or the generator is wrong.
- **Latency that falls when drops rise** is still not an improvement. Report latency together with both kinds of drops.

## What we are optimizing

Proposed goal, extending the packet routing goal:

> Find the least total server capacity that keeps overload loss of allowed packets below $X$ and their P90 end-to-end latency below $Y$, while every packet the list denies is dropped.

$X$ and $Y$ have not been chosen. As in the packet routing network, $Y$ should be a multiple of zero-load latency, here the number of routers on the path times $E[W_f]$.

The settings we can change are server placement and buffer sizes, as before, plus:

- **The length of the list.** Each rule costs every packet $r$ at every router.
- **The order of the list.** Rules that match often belong at the top, so packets stop checking early. In this model every packet checks every rule, which is the worst case.
- **Where filtering happens.** With the same list everywhere, every denial already happens at the first router. Checking again at every later router costs every allowed packet $(\text{hops} - 1) \cdot N r$ and stops nothing more. Filtering only at the routers where traffic enters should block the same packets at a lower cost. That is the main prediction a run can check.

## Planned study

The same seven sets as the packet routing studies: a baseline server budget, then −10%, −20%, −30%, +25%, +50%, and +100%. Each has one traffic seed and the six main plots, plus the Objective A tools. The study plan adds the firewall cost to each router's predicted load, so the baseline still runs its busiest queue at 70% utilization.

Starting values, to be confirmed:

| Setting | Value | Effect |
| --- | --- | --- |
| Rules $N$ | 50 | |
| Time per rule $r$ | 0.2 ms | $N r$ = 10 ms, on top of the routing $W$ of about 50 ms, so 20% more work per visit |
| Unwanted share $p$ | 10% | |

Each model is the packet routing model of the same scale with the three firewall settings added:

| Model | Lab scale | Routers | Queues | Packets per run | Where it runs |
| --- | --- | ---: | ---: | ---: | --- |
| Simple | 10s of queues | 10 | 10 | 5,000 | This VM |
| Medium | 100s of queues | 200 | 200 | 10,000 | Sooner |
| Large | 1000s of queues | 1,000 | 1,000 | 100,000 | Sooner |
| Very large | 10,000s of queues | 3,400 | 10,200 | 100,000 | Sooner, on a large-memory node |

The very large model uses the decomposed form, so its routers check the list in the validate stage. The large and very large packet routing models scale their service times by 2 and 5, and the time per rule $r$ scales with them, so the list adds the same 20% of work per visit at every scale.

## What is added

The firewall's code is in this folder:

| Path | Role |
| --- | --- |
| [fw/firewall.py](fw/firewall.py) | Load the list, check it first match wins, and write the generated list |
| [fw/router.py](fw/router.py) | The packet routing router with the check in its visit: spend $N r$ and log `ERROR FIREWALL-DENY` |
| [server_func.py](server_func.py) | The server function for the monolith and the validate stage, with `--acl` and `--rule-wait` |
| [fw/extension.py](fw/extension.py) | What a firewall network adds to a packet routing run: write the list, start these server functions, pass the blocked share, and count denials |
| [tests/](tests/test_firewall.py) | The firewall tests |

It builds on the packet routing code, which has small hooks for it:

| Packet routing file | Hook |
| --- | --- |
| `pk/router.py` | A visit asks `_admit` before `EXITS-SERVER` and calls `_reject` after it. Packet routing admits every packet. |
| `pk/runtime.py` | `run_cli` takes extra options and a router factory |
| `run_local.py` | `run_network` takes an optional extension |
| Workload generator | `--blocked-share p`. Without it, the generator's output is unchanged |
| `analysis/obj-b-packets/run_study.py` | The firewall cost in the predicted load, denied packets apart from overload loss, and the extension for a study with `firewall_rules` |

`analysis/obj-b-firewall/` has the study files and Slurm jobs of the four models.

The gRPC `Job` message, the Objective A code, and the C++ port do not change. The firewall studies use the Python server functions.

## Run

The setup is the packet routing one: the venv from the [studies README](../../analysis/obj-b-packets/README.md#run-on-this-vm). From the repository root, this runs a model's seven sets on this machine:

```text
~/packets-venv/bin/python analysis/obj-b-packets/run_study.py obj-b-firewall/simple
```

On Sooner, each model has its own Slurm job:

```text
sbatch analysis/obj-b-firewall/MODEL/run.sbatch
```

`MODEL` is `simple`, `medium`, `large`, or `very-large`. Results go to `analysis/obj-b-firewall/MODEL/results/`, and the raw logs to `/scratch/$USER/obj-b-firewall-MODEL/JOBID/`. The [studies README](../../analysis/obj-b-packets/README.md#run-on-sooner) covers copying the code, the one-time setup, and bringing the plots back.

## Tests

From the repository root:

```text
python -m unittest discover -s implementation/obj-b.firewall/tests -v
```

`test_firewall.py` covers rule matching, the generated list, the visits with a firewall, the blocked share of traffic, the predicted load, and two small networks, monolithic and decomposed, in which every packet is delivered or denied. The packet routing tests run unchanged.
