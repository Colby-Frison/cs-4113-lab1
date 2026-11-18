# Lab 1: Queuing Lab Part II

CS-4/5113 — Distributed Operating Systems, Fall 2026.  
**Group 2:** Colby Frison (manager), Jhan Paul Mackey, Felix Werner, Ana Moron Cervantes, Taylor Wise.

## What this repo is

We built a shared queueing framework (Objective A), then each person cast a real process onto it (Objective B) and studied how it behaves when you change resources. Objective C covers orchestration and monitoring so larger runs are actually runnable.

The short version of the lab: figure out what is a job, what is a queue, and what is a server; measure USE / throughput / latency; then move the knobs and see what actually bottlenecks.

## Who did what

| Person        | Focus                                                                                                 |
| ------------- | ----------------------------------------------------------------------------------------------------- |
| **Colby**     | Manager - ranked **matchmaking** (`implementation/obj-b.matchmaking/`).                              |
| **Jhan Paul** | **Food delivery** kitchen and courier queues (`obj-b.food-delivery`, branch `obj-b/food-delivery`). |
| **Ana**       | Tech lead - **Customer support ticketing** (`obj-b.ticketing`, branches `obj-b/ticketing` / `ana-testing`).       |
| **Taylor**    | **Smart-lock firmware scanning** (`obj-b` lock/scanner work, branch `obj-b-lock-app`).                |
| **Felix**     | **Packet / firewall routing** apps (branches `obj-b/packets`, `obj-b/firewall`).                      |

Shared Objective A lives under `implementation/obj-a/` (queues, server functions, workload generator, analysis tools, wait-job sanity checks). Course template code is in `example_code/` and we leave it alone.

Tech lead tasks were split up amongst members to distribute the work instead of dropping it all onto one person. Split of work can be seen on the tasksheet.

## How the work is organized

Not everything is merged to `main`. Each app mostly lives on its own branch so people could finish studies without stepping on each other. A full hand-in is a **git bundle of all branches**.

Useful entry points once you check out the right branch:

- `implementation/obj-a/README.md` — framework and tools  
- `implementation/obj-b.../README.md` — that member’s casting and how to run it  
- Study notes and plots sit with each app’s `studies/` (or analysis folders on that branch)

## Meeting / sync

We use class lab time as the main sync, and chat outside class when something is blocked. Day-to-day work is async on GitLab branches; the manager keeps the shared Obj A / tech-lead pieces moving so domain studies can run.

## Deliverables (for graders)

- Code: this repo / the all-branches git bundle  
- Write-up: final report (LaTeX)  
- Talk: ~15 min presentation (casting + sensitivity, not a poster)  
- Tasksheet / team assessment: submitted separately per course instructions
