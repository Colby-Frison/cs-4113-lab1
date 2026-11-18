# Lab 1: Queuing Lab Part II

CS-4/5113 — Distributed Operating Systems, Fall 2026.  
**Group 2:** Colby Frison (manager), Jhan Paul Mackey, Felix Werner, Ana Moron Cervantes, Taylor Wise.

## What this repo is

We built a shared queueing framework (Objective A), then each person cast a real process onto it (Objective B) and studied how it behaves when you change resources. Objective C covers orchestration and monitoring so larger runs are actually runnable.

The short version of the lab: figure out what is a job, what is a queue, and what is a server; measure USE / throughput / latency; then move the knobs and see what actually bottlenecks.

## Who did what

| Person        | Focus                                                                 |
| ------------- | --------------------------------------------------------------------- |
| **Colby**     | Manager — ranked **matchmaking** (implementation/obj-b.matchmaking/) |
| **Jhan Paul** | **Food delivery** kitchen and courier queues (implementation/obj-b.food-delivery/) |
| **Ana**       | Tech lead — **Customer support ticketing** (implementation/obj-b.ticketing/) |
| **Taylor**    | **Smart-lock firmware scanning** (implementation/obj-b/smart-lock/) |
| **Felix**     | **Packet / firewall routing** (implementation/obj-b.packets/, implementation/obj-b.firewall/) |

Shared Objective A lives under implementation/obj-a/ (queues, server functions, workload generator, analysis tools, wait-job sanity checks). Course template code is in example_code/ and we leave it alone.

Tech lead tasks were split up amongst members to distribute the work instead of dropping it all onto one person. Split of work can be seen on the tasksheet.

## Layout

This tree combines the Objective A framework with every Objective B application and its study plots on one branch.

Useful entry points:

- implementation/obj-a/README.md — framework and tools
- implementation/obj-b.matchmaking/README.md — matchmaking casting and studies
- implementation/obj-b.food-delivery/README.md — food delivery casting and studies
- implementation/obj-b.ticketing/README.md — ticketing casting and studies
- implementation/obj-b/smart-lock/README.md — smart-lock casting and studies
- implementation/obj-b.packets/README.md / implementation/obj-b.firewall/README.md — packet and firewall casting
- Study notes and plots sit with each app under studies/ or nalysis/

Generated run logs and tool CSVs are gitignored; figures and summary.md files stay.

## Meeting / sync

We use class lab time as the main sync, and chat outside class when something is blocked.

## Deliverables (for graders)

- Code: this repository
- Write-up: final report (LaTeX)
- Talk: ~15 min presentation (casting + sensitivity, not a poster)
- Tasksheet / team assessment: submitted separately per course instructions
