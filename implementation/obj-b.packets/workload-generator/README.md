# Packet workload generator

Writes the packet stream for one source router, one flushed row per packet. Each source router runs its own copy, feeding that router's first queue over a FIFO. The generator stays running during a test because the queue ignores the arrival column and admits a row when it reads it. A finished file would arrive as a burst.

Back to the [application README](../README.md).

```text
Job Id,arrival time,Tasks,Dst IP,TTL
p17-42,1760990100.123,"[""0: W 0.005000"", ""1: W 0.015000"", ""2: W 0.050779""]",10.3.144.7,64
```

## Distribution decisions

| Quantity | Decision | Why |
| --- | --- | --- |
| Time between packets | Exponential, rate `--rate` | Traffic at a router comes from many independent hosts, and combining many independent sources gives roughly Poisson arrivals. It also lets the utilization prediction use a known arrival rate. |
| Destination router | Uniform over every router except the source | No measured traffic matrix goes with this topology. Uniform assumes no favorites, so load differences come from the topology and routing rather than from an invented traffic pattern. |
| Destination host | Uniform from 1 to 254 inside the router's `/24` | Routing only reads the prefix, so this has no effect on a path. It keeps addresses realistic. |
| Packet size | Simple IMIX: 40, 576, and 1500 bytes in a 7 : 4 : 1 ratio | IMIX is a standard stand-in for Internet packet sizes: many small packets and a few large ones. The mean is 340.3 bytes. |
| Number of tasks | Always 3: validate, lookup, forward | These are the three things a router does to a packet. The same tasks are the three decomposed stages. |
| Validate wait (task 0) | Fixed, 0.005 s | Reading a header costs the same whatever the packet carries. |
| Lookup wait (task 1) | Fixed, 0.015 s | The table is the same size for every packet, so one lookup costs the same as another. |
| Forward wait (task 2) | Proportional to packet size, mean 0.03 s | Moving a packet takes time per byte. The three sizes give 3.5, 50.8, and 132.2 ms. |
| TTL | 64 | A common default starting TTL. The longest path in the 1000-router sample is 9 hops, so it never expires on a correct table. |

The mean visit is 0.05 s in total. That sits well above the measured overhead of a few milliseconds per visit, so sleeping dominates the service time. The waits are model values, not measurements of real router hardware, so only their ratios and their size against the arrival rate carry meaning.

A router's service time is therefore a mix of three values, so a router is approximately an M/G/c queue: Poisson arrivals, general service, `c` servers. Downstream routers see the departures of other routers, which are not exactly Poisson, so the network is not an exact Jackson network. The utilization prediction $\rho_i = \lambda_i E[W] / c_i$ still holds, because it depends only on the means. Delay formulas that assume exponential service are only approximations here.

**Known simplification.** Real Internet traffic is burstier than Poisson, so these runs will likely understate queues and drops at the same average load.

## How a run proceeds

1. Write the header and flush it.
2. Draw a gap and sleep it. A `--duration` that would run out during the gap stops the run without a partial packet.
3. Draw the size, destination router, and host. Write one row with the current time, and flush it.
4. Repeat until `--count` or `--duration` is reached, whichever comes first, or until Ctrl-C.

Gaps and packet attributes use separate random streams, each seeded from `--seed` and `--source`. Different sources therefore get different traffic, a seed repeats exactly, and changing `--rate` keeps the same sequence of packets. The arrival times themselves still vary slightly between runs, because they depend on real sleeps. A study should report the measured arrival rate beside the requested one.

The job id is `p{source}-{sequence}`, which is unique across generators without any coordination.

## Flags

```text
python workload_generator.py
  --source N             Index of this source router
  --routers N            Routers in the network
  --rate R               Packets per second from this source
  [--count N]            Stop after N packets
  [--duration SEC]       Stop after SEC seconds
  [--ttl 64]
  [--validate-wait 0.005]
  [--lookup-wait 0.015]
  [--forward-wait-mean 0.03]
  [--seed 1]
  [--out PATH]           File or FIFO (default: stdout)
  [--copy PATH]          Also write every row here
```

At least one of `--count` and `--duration` is required. `--copy` keeps a record of everything offered, which is how a run counts packets sent. Opening a FIFO blocks until it has a reader, so start the queue first.
