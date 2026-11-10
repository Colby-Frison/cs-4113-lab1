
import csv
import json
from pathlib import Path

HEADER = [
    "Sys ID",
    "Queue Max Size",
    "Num. Servers",
    "Server Function",
    "Server Function Rate",
    "Source",
    "Sink",
    "Edges in",
    "Edges out",
]

def edge_list(ids):
    return "[" + ",".join(ids) + "]"

def parse_edges(text):
    text = text.strip()

    if not text or text == "[]":
        return []

    if '"' in text:
        return json.loads(text)

    return [
        part.strip()
        for part in text.strip("[]").split(",")
        if part.strip()
    ]

def build_config(total=6, mode="decomposed", capacity=100000, servers=1):
    if mode == "monolithic":
        stages = ["Monolithic"] * (total - 1) + ["VerifyReport"]
        edges = [[total - 1] for _ in range(total - 1)] + [[]]
        ingress = list(range(total - 1))
    else:
        stages = ["Prepare"] + ["Scan"] * (total - 4) + [
            "Review",
            "Verify",
            "Report",
        ]
        edges = (
            [list(range(1, total - 3))]
            + [[total - 3] for _ in range(total - 4)]
            + [[total - 2], [total - 1], []]
        )
        ingress = [0]

    rows = []

    for i, stage in enumerate(stages):
        row = dict(
            zip(
                HEADER,
                [
                    f"q{i}",
                    capacity,
                    1 if stage in ("Verify", "VerifyReport") else servers,
                    stage,
                    1,
                    f"in{i}.wl" if i in ingress else "",
                    "out.wl" if not edges[i] else "",
                    "[]",
                    edge_list([f"q{j}" for j in edges[i]]),
                ],
            )
        )

        rows.append(row)

    for i, row in enumerate(rows):
        row["Edges in"] = edge_list(
            [
                r["Sys ID"]
                for r in rows
                if f"q{i}" in parse_edges(r["Edges out"])
            ]
        )

    return rows, ingress

def save_config(path, rows):
    with Path(path).open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=HEADER,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def load_config(path):
    with Path(path).open(newline="") as f:
        rows = list(csv.DictReader(f))

    for row in rows:
        row["Queue Max Size"] = int(row["Queue Max Size"])
        row["Num. Servers"] = int(row["Num. Servers"])
        row["edges"] = parse_edges(row["Edges out"])

    return rows
