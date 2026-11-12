# Packet routing studies

Each folder here is one model of the [packet routing application](../../implementation/obj-b.packets/README.md) at one of the lab's scales. Every model runs the same sensitivity study: a baseline and six server budgets (−10%, −20%, −30%, +25%, +50%, +100%), each with the six main plots and the Objective A tools. One script, [run_study.py](run_study.py), runs any model. Each model's `run.sbatch` runs it on one Sooner node.

| Model | Lab scale | Routers | Queues | Processes | Packets per run | Seeds | Sooner node |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| [simple](simple/README.md) | Small: 10s of queues | 10 | 10 | 23 | 5,000 | per `study.json` | `normal`, 16 cores, 32 GB |
| [medium](medium/study.json) | 100s of queues | 200 | 200 | 420 | 10,000 | 1 | `normal`, 32 cores, 64 GB, exclusive |
| [large](large/study.json) | 1000s of queues | 1,000 | 1,000 | 2,100 | 100,000 | 1 | `normal`, 64 cores, 200 GB, exclusive |
| [very-large](very-large/study.json) | 10,000s of queues | 3,400 | 10,200 | 20,740 | 100,000 | 1 | `largemem`, 56 cores, about 2 TB, exclusive |

None of the jobs has run on Sooner yet. The runtimes below are estimates from local runs, so the first job of each size may need its time limit adjusted.

| Model | Arrival rate | Generating one set | Estimated job | Time limit |
| --- | ---: | ---: | ---: | ---: |
| simple | 69 /s | 73 s | 30 minutes with 3 seeds | 3 hours |
| medium | 256 /s | 39 s | 20 to 40 minutes | 2 hours |
| large | 472 /s | 212 s | 1.5 to 3 hours | 8 hours |
| very-large | 450 /s | 222 s | 4 to 8 hours | 24 hours |

## How the models were chosen

- **One queue per router, except at very large.** The topology has 6,474 Autonomous Systems, so one queue per router can't reach 10,000 queues. The very large model uses the decomposed server functions instead: each router is a validate, a lookup, and a forward queue, so 3,400 routers give 10,200 queues. A router's three queues each get the router's server count.
- **One seed above the small model.** It keeps the jobs short. Repeating a set with other traffic only narrows its spread, and the lab's bands are met with one run per set.
- **Servers at least 1.43 per router.** Every router needs one server even at −30%, so the baseline budget has to be at least the router count divided by 0.7. The study then sets the arrival rate so the busiest queue reaches 70% utilization. In the medium, large, and very large plans, that queue is a lightly used router with one server, not the hub.
- **Longer service times at large and very large.** In every sample, one hub router carries 38% to 49% of all packets, and each queue is one Python process. A local measurement gave about 3.8 ms of CPU per router visit, split between the queue and its server function, so a Python router manages a few hundred packets a second. Large doubles every service time and very large multiplies them by 5, which keeps the hub near 220 packets a second. The model is the same, with a slower clock: compare latency between models in multiples of the zero-load latency, not in milliseconds.
- **One node per job.** A multi-node launcher would need trial runs on Sooner to get right. The largest model fits one of Sooner's 2 TB `largemem` nodes instead. Each Python process uses about 45 MB, so the very large model needs about 1 TB.

## Defining a model

A model is a folder with a `study.json`. To add one, copy a folder and change its values:

| Field | Meaning |
| --- | --- |
| `mode` | `monolith` for one queue per router, `stages` for three. Default `monolith` |
| `routers`, `seed`, `topology` | How many routers to sample from the topology, and the sample |
| `sources` | How many routers inject traffic, one generator each |
| `validate_wait`, `lookup_wait`, `forward_wait_mean` | Service time per packet in seconds. Fixed, fixed, then exponential |
| `ttl` | Starting TTL. Every path is checked against it before a run |
| `qsize_per_server`, `min_qsize` | Buffer sizes: `max(min_qsize, qsize_per_server × servers)` at the baseline budget |
| `degree_per_server` | Unused here, because a study always sets a server budget |
| `baseline_servers` | The baseline budget, split in proportion to predicted load, with at least one per router |
| `target_busiest_utilization` | The predicted utilization of the busiest queue at baseline, which sets the arrival rate |
| `packets`, `traffic_seeds` | Packets per run, and one run per seed |
| `sets` | Each set's budget as a multiple of `baseline_servers` |

Before a long run, print the plan without starting anything. It shows the rate and the busiest queues:

```text
~/packets-venv/bin/python -c "import json, sys; sys.path.insert(0, 'analysis/obj-b-packets'); import run_study; p = run_study.plan_study(json.load(open('analysis/obj-b-packets/medium/study.json'))); print(round(p['rate'], 1), p['busiest'])"
```

## Run on this VM

The VM's Python has no gRPC, and no `ensurepip`, so make a venv without pip and fill it with the system's pip:

```text
python3 -m venv --without-pip ~/packets-venv
python3 -m pip --python ~/packets-venv/bin/python install -r implementation/obj-a/requirements.txt numpy pandas matplotlib
```

Then, from the repository root:

```text
~/packets-venv/bin/python analysis/obj-b-packets/run_study.py simple
```

`--only base` runs one set, `--seeds 1` one seed, and `--packets N` overrides the count. Results go to `MODEL/results/`, and raw logs to `MODEL/runs/`. The VM has room for the simple model only. The medium model's 420 processes need about 20 GB.

## Run on Sooner

Log in with `ssh username@sooner.oscer.ou.edu`, and copy files through `data.oscer.ou.edu`. Don't use VS Code Remote SSH, which OSCER warns against.

**1. Copy the code.** On this VM, from the repository root. The excludes leave out local results, which a run regenerates. Keep the `:` at the end of the `scp` line:

```text
tar czf packets.tgz --exclude='analysis/*/*/runs' --exclude='analysis/*/*/results' --exclude='__pycache__' implementation analysis
scp packets.tgz username@data.oscer.ou.edu:
```

**2. Set up once.** On Sooner:

```text
mkdir -p lab1
tar xzf packets.tgz -C lab1
cd lab1
bash analysis/obj-b-packets/sooner/setup.sh
```

This creates `~/packets-venv` and checks that the packages import. To update the code later, repeat step 1 and the `tar xzf` line.

**3. Test one set, then run the model.** From `~/lab1`, for example the medium model:

```text
sbatch --time=00:30:00 analysis/obj-b-packets/medium/run.sbatch --only base
squeue -u username
cat packets-medium-JOBID.out
sbatch analysis/obj-b-packets/medium/run.sbatch
```

Every job first prints the node's cores, memory, and limits, then one line per set. Check that `unaccounted` is 0 on every line. Results are written after each set, so a job that runs out of time keeps the sets it finished. Run the models in order of size, so a problem shows up in a short job.

**4. Bring the plots back.** On Sooner, from `~/lab1`:

```text
MODEL=medium
find analysis/obj-b-packets/$MODEL/results \( -name '*.png' -o -name summary.md -o -name metrics-by-run.csv -o -name plan.json \) | tar czf $MODEL-plots.tgz -T -
```

Then on this VM, from the repository root:

```text
MODEL=medium
scp username@data.oscer.ou.edu:lab1/$MODEL-plots.tgz .
tar xzf $MODEL-plots.tgz
```

The files land in `analysis/obj-b-packets/MODEL/results/`. Each run's raw logs are packed into `/scratch/$USER/packets-MODEL/JOBID/` as soon as the run is analyzed. `/scratch` has no backups and deletes files automatically. For the large models, tool outputs over 50 MB, such as `events.log`, stay in that archive rather than in `results/`, and `tools/not-copied.txt` lists them.

## What the job checks before it starts

[sooner/job.sh](sooner/job.sh) is the body of every `run.sbatch`. It raises the open-file and process limits as far as the node allows, then stops at once with a message if the node can't run the model:

| Exit | Message | What to do |
| ---: | --- | --- |
| 2 | `Submit from the repository root`, or `No Python at` | `cd ~/lab1` before `sbatch`, or run `setup.sh` |
| 3 | `does not support FIFOs` | Set `PACKETS_WORK` to a directory on the node's local disk |
| 3 | `Can't listen on 127.0.0.2` | Only the very large model needs it. Ask support@oscer.ou.edu |
| 4 | `needs about N processes and threads, but the limit is` | Ask support@oscer.ou.edu to raise `nproc` for the job |
| 4 | `Ephemeral ports start at` | The node's outgoing ports overlap 20000 to 31999. Ask support@oscer.ou.edu |

Other problems:

| Symptom | Likely cause | What to try |
| --- | --- | --- |
| `Invalid partition` | Partition names differ from OSCER's pages | Check `sinfo`, and change `--partition` in `run.sbatch` |
| `module: command not found`, or the wrong Python | The container's environment | Check `module avail Python`, and update the module name in `setup.sh` and `job.sh` |
| `ModuleNotFoundError` in the job | The venv doesn't work inside `--container=el9hw` | Run `setup.sh` inside an interactive job on the same container, or ask support@oscer.ou.edu |
| `no gRPC listen line` | Startup took longer than its limit, which grows with the number of processes | Check the node's load in the job output. Raise `READY_SEC_PER_PROCESS` in `run_local.py` |
| `timed out with N packets unaccounted for` | Packets still queued after the generators stopped | Too few servers for the rate. Check the set's `utilization.png` |
| The job is killed for memory | The processes need more than the request | Raise `--mem`, or use the next larger node |

## Files

| Path | Role |
| --- | --- |
| [run_study.py](run_study.py) | Plans and runs a model's sets, and writes its results |
| `MODEL/study.json` | The model's configuration |
| `MODEL/run.sbatch` | The model's Slurm resources |
| [sooner/setup.sh](sooner/setup.sh) | One-time setup on Sooner |
| [sooner/job.sh](sooner/job.sh) | Checks the node, runs the study, and packs the raw logs |
