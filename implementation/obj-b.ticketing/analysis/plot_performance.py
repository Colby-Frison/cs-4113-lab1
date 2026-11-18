
import re
from pathlib import Path

import matplotlib.pyplot as plt

BASE_DIR = Path(__file__).resolve().parents[1]

WORKLOADS = {
    "Small": ("server_small.log", 50),
    "Medium": ("server_medium.log", 200),
    "Large": ("server_large.log", 500),
}


def analyze_log(filename):
    path = BASE_DIR / filename
    starts = []
    ends = []

    for line in path.read_text().splitlines():
        match = re.search(
            r"(\d+\.\d+)\s+\S+\s+IN\s+\S+\s+(ENTERS-SERVER|EXITS-SERVER)",
            line,
        )

        if not match:
            continue

        timestamp = float(match.group(1))
        event = match.group(2)

        if event == "ENTERS-SERVER":
            starts.append(timestamp)
        elif event == "EXITS-SERVER":
            ends.append(timestamp)

    if not starts or not ends:
        raise ValueError(f"No processing events found in {filename}")

    duration = max(ends) - min(starts)
    throughput = len(ends) / duration if duration > 0 else 0

    return duration, throughput, len(ends)


def main():
    labels = []
    durations = []
    throughputs = []

    for label, (filename, expected) in WORKLOADS.items():
        duration, throughput, completed = analyze_log(filename)

        print(
            f"{label}: {completed}/{expected} tickets, "
            f"{duration:.2f}s, {throughput:.2f} tickets/sec"
        )

        if completed != expected:
            raise ValueError(f"{label}: unexpected ticket count")

        labels.append(label)
        durations.append(duration)
        throughputs.append(throughput)

    output = BASE_DIR / "analysis" / "graphs"
    output.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(8, 5))
    plt.bar(labels, durations)
    plt.title("Ticket Processing Time by Workload")
    plt.xlabel("Workload Size")
    plt.ylabel("Processing Time (seconds)")
    plt.tight_layout()
    plt.savefig(output / "processing_time.png")
    plt.close()

    plt.figure(figsize=(8, 5))
    plt.bar(labels, throughputs)
    plt.title("Ticket Processing Throughput by Workload")
    plt.xlabel("Workload Size")
    plt.ylabel("Tickets Processed per Second")
    plt.tight_layout()
    plt.savefig(output / "throughput.png")
    plt.close()

    print(f"Graphs saved to: {output}")


if __name__ == "__main__":
    main()
