
import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

root = Path("runs/full")
rows = []

for summary in root.rglob("summary.json"):
    relative = summary.relative_to(root)

    # Only include small and medium workloads
    if relative.parts[0] not in ("small", "medium"):
        continue

    run = summary.parent
    metrics = run / "sysmetrics.csv"

    if not metrics.exists():
        matches = list(run.rglob("sysmetrics.csv"))
        if not matches:
            continue
        metrics = matches[0]

    data = json.loads(summary.read_text())
    df = pd.read_csv(metrics)

    if not {"Avg-response-time", "throughput"}.issubset(df.columns):
        continue

    valid = df.dropna(subset=["Avg-response-time", "throughput"])
    valid = valid[valid["throughput"] > 0]

    if valid.empty:
        continue

    mean_latency = (
        (valid["Avg-response-time"] * valid["throughput"]).sum()
        / valid["throughput"].sum()
    )

    name = str(run.relative_to(root))
    if name.endswith("/outputs"):
        name = name[:-8]

    rows.append((name, mean_latency))

if not rows:
    raise SystemExit("No usable small or medium sysmetrics.csv files found")

rows.sort()

names = [row[0] for row in rows]
latencies = [row[1] for row in rows]

plt.figure(figsize=(max(10, len(rows) * 0.65), 5))
plt.bar(names, latencies)
plt.xticks(rotation=60, ha="right")
plt.ylabel("Mean end-to-end latency (ms)")
plt.title("Smart-lock scanner sensitivity analysis (small and medium)")
plt.tight_layout()
plt.savefig("sensitivity-latency.png", dpi=150)

print(f"Saved sensitivity-latency.png using {len(rows)} runs")
