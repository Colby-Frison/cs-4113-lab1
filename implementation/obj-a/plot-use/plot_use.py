#!/usr/bin/env python3
"""plot-use — Queue Lab Objective C.

Draw the USE CSV from compute-use as three stacked panels that share the
time axis: utilization, saturation, and error rate. Each queue has one hue.
Windows 10, 100, and 1000 are a light line, a solid line, and a dark dashed
line of that hue. A single queue is drawn in the slide colors: blue
utilization, a yellow saturation area, and a red error rate.

Usage:
    python plot_use.py --quse quse.csv [--qid 0[,1,...]] [--out use.png]
                        [--title TEXT] [--xlim A:B]
"""

from __future__ import annotations

import argparse
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import pandas as pd

WINDOWS = (10, 100, 1000)
REQUIRED = ["time-step", "queue-sys-id"] + [
    name
    for size in WINDOWS
    for name in (f"Utilization({size})", f"Saturation({size})", f"Errors({size})")
]

# One hue per queue when several queues share the figure.
PALETTE = (
    "#2a78d6",
    "#eb6834",
    "#008300",
    "#7b4db5",
    "#c43d6e",
    "#b86e00",
    "#0e7c86",
    "#5c6b2f",
    "#3d3d8f",
    "#a33b24",
    "#4d6a8a",
)
# The slide figure uses one color per panel. A single queue follows that.
SLIDE_HUE = {"Utilization": "#2a78d6", "Saturation": "#e6b422", "Errors": "#d23b3b"}
# Shorter windows are lighter. The 1000-step window is dashed, as in the course sketch.
WINDOW_TINT = {10: 0.62, 100: 0.28, 1000: 0.0}
WINDOW_WIDTH = {10: 1.15, 100: 1.7, 1000: 2.15}
WINDOW_STYLE = {10: "-", 100: "-", 1000: "--"}
COLORS = {
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
    "ink": "#0b0b0b",
    "muted": "#52514e",
}
_QID_RE = re.compile(r"(\d+)$")


def parse_pair(value: str) -> tuple[float, float]:
    parts = value.split(":")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("expected A:B")
    try:
        return float(parts[0]), float(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("bounds must be numbers") from exc


def shade(hex_color: str, toward_white: float) -> str:
    channels = [int(hex_color[i : i + 2], 16) for i in (1, 3, 5)]
    mixed = [int(channel + (255 - channel) * toward_white) for channel in channels]
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


def load_quse(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = [name for name in REQUIRED if name not in df.columns]
    if missing:
        raise SystemExit(f"plot-use: quse CSV missing columns: {missing}")
    if df.empty:
        raise SystemExit("plot-use: quse CSV has no rows")
    df["queue-sys-id"] = df["queue-sys-id"].astype(str)
    return df.sort_values("time-step").reset_index(drop=True)


def _style_axis(ax) -> None:
    ax.grid(True, color=COLORS["grid"], linewidth=1, linestyle="-")
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(COLORS["axis"])
    ax.tick_params(colors=COLORS["muted"], labelsize=9)
    ax.yaxis.label.set_color(COLORS["ink"])
    ax.xaxis.label.set_color(COLORS["ink"])
    ax.title.set_color(COLORS["ink"])


def normalize_qid(token: str) -> str:
    """Map ``q0`` / ``007`` to the numeric id stored in quse.csv."""
    match = _QID_RE.search(token.strip())
    if not match:
        raise ValueError(f"invalid queue id {token!r}")
    return str(int(match.group(1)))


def parse_qids(text: str) -> list[str]:
    found: list[str] = []
    for token in text.split(","):
        if not token.strip():
            continue
        qid = normalize_qid(token)
        if qid not in found:
            found.append(qid)
    if not found:
        raise ValueError("no queue ids in --qid")
    return found


def select_queues(df: pd.DataFrame, qids: list[str] | None) -> pd.DataFrame:
    if not qids:
        return df
    present = set(df["queue-sys-id"])
    missing = [qid for qid in qids if qid not in present]
    if missing:
        raise SystemExit(f"plot-use: quse CSV has no rows for queue {missing[0]}")
    return df[df["queue-sys-id"].isin(qids)].reset_index(drop=True)


def _draw_series(ax, time, values, color: str, size: int) -> None:
    ax.plot(
        time,
        values,
        color=color,
        linewidth=WINDOW_WIDTH[size],
        linestyle=WINDOW_STYLE[size],
        alpha=0.95 if size != 10 else 0.75,
        solid_capstyle="round",
    )


MAX_AUTO_QUEUES = 8


def select_salient_queues(df: pd.DataFrame, limit: int = MAX_AUTO_QUEUES) -> list[str]:
    """Keep the figure readable when a fan-out casting has dozens of queues.

    Prefer the busiest saturation peaks so shared gates and hot shards remain
    visible; drop quiet replicas that only thicken the hairball.
    """
    queues = list(dict.fromkeys(df["queue-sys-id"]))
    if len(queues) <= limit:
        return queues
    peaks: list[tuple[float, str]] = []
    for qid in queues:
        series = df[df["queue-sys-id"] == qid]["Saturation(100)"]
        peaks.append((float(series.max()) if not series.empty else 0.0, qid))
    peaks.sort(reverse=True)
    chosen = [qid for _peak, qid in peaks[:limit]]
    # Stable legend order by numeric id when possible.
    def sort_key(qid: str) -> tuple[int, str]:
        try:
            return (0, f"{int(qid):06d}")
        except ValueError:
            return (1, qid)

    return sorted(chosen, key=sort_key)


def _downsample_frame(series: pd.DataFrame, limit: int = 2500) -> pd.DataFrame:
    if len(series) <= limit:
        return series
    idx = (
        (pd.Series(range(len(series))) * (limit - 1) / max(len(series) - 1, 1))
        .round()
        .astype(int)
        .unique()
    )
    return series.iloc[idx].reset_index(drop=True)


def plot_use(
    df: pd.DataFrame,
    out: str,
    title: str | None = None,
    xlim: tuple[float, float] | None = None,
    max_queues: int | None = MAX_AUTO_QUEUES,
    labels: dict[str, str] | None = None,
) -> None:
    queues = list(dict.fromkeys(df["queue-sys-id"]))
    if not queues:
        raise SystemExit("plot-use: quse CSV has no rows")
    omitted = 0
    if max_queues is not None and len(queues) > max_queues:
        keep = set(select_salient_queues(df, max_queues))
        omitted = len(queues) - len(keep)
        df = df[df["queue-sys-id"].isin(keep)].reset_index(drop=True)
        queues = list(dict.fromkeys(df["queue-sys-id"]))
    one_queue = len(queues) == 1
    labels = labels or {}
    # Many-queue casts: one long window only. Dual windows turn into a hairball.
    windows = (1000,) if len(queues) > 1 else WINDOWS
    fig, (ax_u, ax_s, ax_e) = plt.subplots(3, 1, figsize=(10, 6.4), sharex=True)
    util_peak = float(df["Utilization(1000)"].max()) if "Utilization(1000)" in df else float(
        df["Utilization(100)"].max()
    )
    util_ylim = (0, 1.05) if util_peak <= 1.05 else (0, util_peak * 1.08)
    panels = (
        (ax_u, "Utilization", "Utilization", util_ylim),
        (ax_s, "Saturation", "Saturation", None),
        (ax_e, "Errors", "Errors (QUEUE-FULL)", None),
    )

    for index, qid in enumerate(queues):
        series = _downsample_frame(df[df["queue-sys-id"] == qid])
        time = series["time-step"]
        for ax, prefix, _ylabel, _ylim in panels:
            hue = SLIDE_HUE[prefix] if one_queue else PALETTE[index % len(PALETTE)]
            for size in windows:
                color = shade(hue, WINDOW_TINT[size])
                values = series[f"{prefix}({size})"]
                # Area fill only for a single queue; multi-queue fills occlude each other.
                if prefix == "Saturation" and one_queue and size == windows[0]:
                    ax.fill_between(time, values, color=color, alpha=0.35, linewidth=0)
                    continue
                _draw_series(ax, time, values, color, size)

    for ax, _prefix, ylabel, ylim in panels:
        ax.set_ylabel(ylabel)
        _style_axis(ax)
        if ylim is not None:
            ax.set_ylim(*ylim)
        else:
            ax.set_ylim(bottom=0)
    ax_e.set_xlabel("time (ms from first event)")
    if xlim is not None:
        ax_e.set_xlim(*xlim)

    legend_rows = []
    if len(windows) > 1:
        for size in windows:
            legend_rows.append(
                Line2D(
                    [0],
                    [0],
                    color="#6e6d68" if size != 1000 else COLORS["ink"],
                    lw=WINDOW_WIDTH[size],
                    ls=WINDOW_STYLE[size],
                    label=f"{size} steps",
                )
            )
    if not one_queue:
        for index, qid in enumerate(queues):
            legend_rows.append(
                Line2D(
                    [0],
                    [0],
                    color=PALETTE[index % len(PALETTE)],
                    lw=2.0,
                    label=labels.get(qid, f"q{qid}"),
                )
            )
    elif windows == WINDOWS:
        for size in windows:
            legend_rows.append(
                Line2D(
                    [0],
                    [0],
                    color="#6e6d68" if size != 1000 else COLORS["ink"],
                    lw=WINDOW_WIDTH[size],
                    ls=WINDOW_STYLE[size],
                    label=f"{size} steps",
                )
            )
    if omitted:
        legend_rows.append(
            Line2D([0], [0], color="none", label=f"+{omitted} quiet queues hidden")
        )
    fig.legend(
        handles=legend_rows,
        loc="center left",
        bbox_to_anchor=(1.0, 0.5),
        frameon=False,
        labelcolor=COLORS["ink"],
        fontsize=8,
    )
    if title:
        fig.suptitle(title, color=COLORS["ink"])
    fig.tight_layout()
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plot USE windows from a quse CSV.")
    parser.add_argument("--quse", required=True, help="CSV written by compute-use")
    parser.add_argument("--out", default="use.png", help="Output PNG path (default: use.png)")
    parser.add_argument(
        "--qid",
        default=None,
        help="Queue id or comma-separated list (q0 and 0 are the same). Default: every queue",
    )
    parser.add_argument(
        "--max-queues",
        type=int,
        default=MAX_AUTO_QUEUES,
        help=(
            f"When plotting every queue, keep only the busiest N by saturation "
            f"(default {MAX_AUTO_QUEUES}; 0 = no cap)"
        ),
    )
    parser.add_argument(
        "--labels",
        default=None,
        help="Optional legend names as qid:name pairs, e.g. 0:admit,3:search,4:ready",
    )
    parser.add_argument("--title", default=None, help="Figure title")
    parser.add_argument("--xlim", type=parse_pair, default=None, help="Time-step limits as A:B")
    return parser


def parse_labels(text: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for token in text.split(","):
        if not token.strip():
            continue
        if ":" not in token:
            raise ValueError(f"expected qid:name in {token!r}")
        qid_text, name = token.split(":", 1)
        labels[normalize_qid(qid_text)] = name.strip()
    return labels


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    df = load_quse(args.quse)
    if args.qid is not None:
        try:
            qids = parse_qids(args.qid)
        except ValueError as exc:
            raise SystemExit(f"plot-use: {exc}") from exc
        df = select_queues(df, qids)
    labels = None
    if args.labels is not None:
        try:
            labels = parse_labels(args.labels)
        except ValueError as exc:
            raise SystemExit(f"plot-use: {exc}") from exc
    max_queues = None if args.max_queues <= 0 or args.qid is not None else args.max_queues
    plot_use(df, args.out, args.title, args.xlim, max_queues=max_queues, labels=labels)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
