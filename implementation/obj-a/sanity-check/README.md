# Objective A sanity checks

Wait-job scale studies for the shared Lab 1 framework. Each band lives in its own directory under this folder. A sanity check is **not** a domain model: it copies the wait-job queue system (CSV source, one queue, one stateless server function, one sink) until the band's queue and job counts are met. Domain work belongs under `implementation/obj-b.*/` and `analysis/obj-b.*/`.

| Band | Directory | Queues | Jobs | Status |
| --- | --- | --- | --- | --- |
| MEDIUM | [medium/](medium/README.md) | 100 | 10,000 | Current run |

Regenerate the medium study:

```text
python implementation/obj-a/sanity-check/medium/run_medium.py
```
