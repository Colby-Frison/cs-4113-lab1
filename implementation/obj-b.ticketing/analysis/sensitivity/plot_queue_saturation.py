
from pathlib import Path
import matplotlib.pyplot as plt

folder = Path(__file__).resolve().parent

# Configuration, queue capacity, and log filename
configs = [
    ("-30%", 70, "minus30_queue.log"),
    ("-20%", 80, "minus20_queue.log"),
    ("-10%", 90, "minus10_queue.log"),
    ("Baseline", 100, "baseline_queue.log"),
    ("+25%", 125, "plus25_queue.log"),
    ("+50%", 150, "plus50_queue.log"),
    ("+100%", 200, "plus100_queue.log"),
]

labels = []
saturation = []

for label, capacity, filename in configs:
    log_file = folder / filename
    measurements = []

    for line in log_file.read_text().splitlines():
        if "metric-q-size" in line:
            size = int(line.split("metric-q-size ")[1].strip())
            measurements.append(size)

    if not measurements:
        raise ValueError(f"No queue measurements found in {filename}")

    peak = max(measurements)
    percent = (peak / capacity) * 100

    labels.append(label)
    saturation.append(percent)

    print(f"{label}: {peak}/{capacity} = {percent:.1f}%")

plt.figure(figsize=(9, 5))
bars = plt.bar(labels, saturation)

for bar, value in zip(bars, saturation):
    plt.text(
        bar.get_x() + bar.get_width() / 2,
        value + 1,
        f"{value:.1f}%",
        ha="center"
    )

plt.title("Peak Queue Saturation by Configuration")
plt.xlabel("Queue Capacity Configuration")
plt.ylabel("Peak Queue Saturation (%)")
plt.ylim(0, 115)
plt.grid(axis="y", alpha=0.3)
plt.tight_layout()

output = folder / "peak_queue_saturation.png"
plt.savefig(output, dpi=300)
plt.close()

print(f"Graph saved to: {output}")
