
#!/usr/bin/env python3
"""Smart-lock workloads, preserving lab0's CSV schema and arrival distributions."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config_io import build_config, save_config
from lab0_workload_generator import make_draw, parse_interval, validate_args, write_workload

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--interval", type=parse_interval, default=(0, 9999))
    parser.add_argument(
        "--dist",
        choices=["fixed", "uniform", "normal", "geometric", "poisson"],
        default="poisson",
    )

    parser.add_argument("--fixed-jobs", type=int, default=2)
    parser.add_argument("--low", type=int, default=0)
    parser.add_argument("--high", type=int, default=4)
    parser.add_argument("--mu", type=float, default=2)
    parser.add_argument("--sigma", type=float, default=1)
    parser.add_argument("--p", type=float, default=0.5)
    parser.add_argument("--lam", type=float, default=2)

    parser.add_argument(
        "--checks",
        nargs="+",
        choices=["authorization", "input_bounds", "replay"],
        default=["authorization", "input_bounds", "replay"],
    )
    parser.add_argument("--passes", type=int, default=1)
    parser.add_argument("--seed", type=int, default=5113)
    parser.add_argument("--queues", type=int, default=6)
    parser.add_argument(
        "--mode",
        choices=["monolithic", "decomposed"],
        default="decomposed",
    )
    parser.add_argument("--servers", type=int, default=1)
    parser.add_argument("--capacity", type=int, default=100000)

    return parser

def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.task_mode = "dist"

    validate_args(args)

    root = args.source_root.resolve(strict=True)
    out = args.out_dir.resolve()
    files = []

    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)

        if path.suffix.lower() not in {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".ino"}:
            continue

        if any(part in {".git", ".pio", "build", "node_modules"} for part in rel.parts):
            continue

        if any(
            (root / Path(*rel.parts[:i])).is_symlink()
            for i in range(1, len(rel.parts) + 1)
        ):
            continue

        if not path.is_file():
            continue

        files.append((rel, path.read_bytes()))

    checks = list(dict.fromkeys(args.checks))

    plans = [
        (rel, data, check, n)
        for n in range(args.passes)
        for rel, data in files
        for check in checks
    ]

    rng = random.Random(args.seed)
    draw = make_draw(args.dist, args, rng)
    arrivals = []

    for tick in range(args.interval[0], args.interval[1] + 1):
        arrivals.extend(
            [tick] * min(draw(), len(plans) - len(arrivals))
        )

        if len(arrivals) == len(plans):
            break

    random.Random(args.seed + 1).shuffle(plans)

    configs, ingress = build_config(
        args.queues,
        args.mode,
        args.capacity,
        args.servers,
    )

    out.mkdir(parents=True, exist_ok=True)

    for rel, data in files:
        target = out / "corpus" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    rows = []
    byqueue = {q: [] for q in ingress}

    with (out / "jobs.jsonl").open("w") as f:
        for i, (rel, data, check, pass_id) in enumerate(plans):
            work = max(1, math.ceil(len(data) / 1024))

            if args.mode == "monolithic":
                costs = [work + 3, 1]
            else:
                costs = [1, work, 1, 1, 1]

            row = {
                "Job Id": f"j{i}",
                "arrival time": arrivals[i],
                "Tasks": [
                    f"{tid}: W {w}"
                    for tid, w in enumerate(costs)
                ],
            }

            q = ingress[i % len(ingress)]
            rows.append(row)
            byqueue[q].append(row)

            f.write(
                json.dumps(
                    {
                        "job_id": f"j{i}",
                        "source": "corpus/" + rel.as_posix(),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "check": check,
                        "bytes": len(data),
                        "pass_id": pass_id,
                    }
                ) + "\n"
            )

    workload_files = [("workload.csv", rows)]

    for q, jobs in byqueue.items():
        workload_files.append((f"in{q}.wl", jobs))

    for name, jobs in workload_files:
        with (out / name).open("w", newline="") as f:
            write_workload(jobs, f)

    save_config(out / "qconf.csv", configs)

    manifest = {
        "schema_version": 2,
        "jobs": len(rows),
        "source_files": len(files),
        "checks": checks,
        "passes": args.passes,
        "queues": args.queues,
        "mode": args.mode,
        "seed": args.seed,
        "dist": args.dist,
        "interval": args.interval,
        "arrival_parameters": {
            k: getattr(args, k)
            for k in ["fixed_jobs", "low", "high", "mu", "sigma", "p", "lam"]
        },
        "work_costs": "W counts are legacy-compatible size estimates; runtime executes real functions, not Sub-X",
        "source_root": str(root),
        "queue_organization": "actual per-node FCFS queues with logical server slots backed by a bounded shared worker pool",
    }

    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )

    print(
        f"Generated {len(rows)} jobs; "
        f"{args.queues} queues; "
        f"{args.mode}; {out}"
    )

if __name__ == "__main__":
    main()
