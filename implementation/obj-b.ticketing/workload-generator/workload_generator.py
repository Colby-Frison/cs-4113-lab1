
import argparse
import csv
import random
from pathlib import Path


DESCRIPTIONS = [
    "I cannot login to my account",
    "I forgot my password",
    "I have a billing problem",
    "My payment was declined",
    "My application is not working",
    "I need help with my order",
]


def generate_workload(count, output, arrival_interval=0.1, seed=42):
    random.seed(seed)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["Job Id", "arrival time", "Tasks"])

        for i in range(count):
            description = random.choice(DESCRIPTIONS)
            category = description.lower().replace(" ", "_")
            job_id = f"T{i:04d}_{category}"

            tasks = '["1: W 0.010000", "2: W 0.020000", "3: W 0.010000"]'
            writer.writerow([job_id, round(i * arrival_interval, 3), tasks])

    print(f"Generated {count} support tickets in {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--out", default="tickets.csv")
    parser.add_argument("--arrival-interval", type=float, default=0.1)

    args = parser.parse_args()

    generate_workload(
        args.count,
        args.out,
        args.arrival_interval,
    )
