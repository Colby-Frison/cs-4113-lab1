
from pathlib import Path
from datetime import datetime
import matplotlib.pyplot as plt

folder = Path(__file__).resolve().parent
log_file = folder / "baseline_queue.log"

times = []
queue_sizes = []

for line in log_file.read_text().splitlines():
    if "metric-q-size" not in line:
        continue

    timestamp = line.split(" - ")[0]
    size = int(line.split("metric-q-size ")[1].strip())

    times.append(datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S,%f"))
    queue_sizes.append(size)

# Keep the active experiment and one sample on each side.
active = [i for i, size in enumerate(queue_sizes) if size > 0]

if not active:
    raise ValueError("No nonzero queue measurements found.")

start = max(0, active[0] - 1)
end = min(len(times), active[-1] + 2)

start_time = times[start]
seconds = [(t - start_time).total_seconds() for t in times[start:end]]
sizes = queue_sizes[start:end]

plt.figure(figsize=(9, 5))
plt.plot(seconds, sizes, marker="o", markersize=3)
plt.axhline(100, linestyle="--", label="Queue capacity (100)")

plt.title("Baseline Queue Occupancy Over Time")
plt.xlabel("Time (seconds)")
plt.ylabel("Tickets Waiting in Queue")
plt.legend()
plt.grid(True, alpha=0.3)
plt.tight_layout()

output = folder / "queue_occupancy_over_time.png"
plt.savefig(output, dpi=300)
plt.close()

print(f"Graph saved to: {output}")
