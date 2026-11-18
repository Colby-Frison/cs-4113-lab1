"""Seeded synthetic orders with independent exponential interarrival times."""

import argparse
import math
import random
import sys
import time

from lib.job import HEADER, Task, format_job_line
from fd.order import Order, ZONES, WAIT_PRECISION


def nonnegative(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be finite and >= 0")
    return number


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return number


def generate(
    out,
    *,
    count=100,
    seed=1,
    arrival_mean=0.04,
    time_scale=1.0,
    sleep=time.sleep,
    clock=time.time,
    restaurant_weights=None,
):
    """Times below are model seconds; scale both arrival and service once.

    count=None streams until interrupted. A zero arrival mean creates a burst.
    Separate random streams keep service attributes fixed if arrivals change.
    """
    if restaurant_weights is not None and (
        not restaurant_weights or any(not math.isfinite(w) or w <= 0 for w in restaurant_weights)
    ):
        raise ValueError("restaurant weights must be finite and positive")
    # independent streams keep order attributes paired when arrival or routing settings change
    restaurant_rng = random.Random(seed + 2)
    arrival_rng = random.Random(seed)
    attribute_rng = random.Random(seed + 1)
    out.write(HEADER + "\n")
    out.flush()
    seq = 0
    while count is None or seq < count:
        delay = arrival_rng.expovariate(1 / arrival_mean) if arrival_mean else 0
        sleep(delay * time_scale)
        zone = attribute_rng.choice(ZONES)
        items = attribute_rng.randint(1, 4)
        preparation_seconds = attribute_rng.uniform(0.02, 0.04) * items
        # near, mid, and far multiply the base travel time by 1, 2, and 3
        travel_seconds = attribute_rng.uniform(0.04, 0.06) * (ZONES.index(zone) + 1)
        restaurant = (
            restaurant_rng.choices(range(len(restaurant_weights)), weights=restaurant_weights)[0]
            if restaurant_weights
            else 0
        )
        tasks = (Task(0, preparation_seconds * time_scale), Task(1, travel_seconds * time_scale))
        out.write(
            format_job_line(
                Order(seq, restaurant, zone, items).job_id,
                clock(),
                tasks,
                wait_precision=WAIT_PRECISION,
            )
            + "\n"
        )
        out.flush()
        seq += 1
    return seq


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=positive_int, help="Omit to stream until Ctrl-C")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--arrival-mean", type=nonnegative, default=0.04)
    parser.add_argument("--time-scale", type=nonnegative, default=1.0)
    parser.add_argument("--out")
    parser.add_argument("--record", help="Also save emitted rows to a regular file")
    args = parser.parse_args()
    out = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout
    record = open(args.record, "w", encoding="utf-8") if args.record else None

    class Tee:
        def write(self, text):
            out.write(text)
            if record:
                record.write(text)

        def flush(self):
            out.flush()
            if record:
                record.flush()

    try:
        generate(
            Tee(),
            count=args.count,
            seed=args.seed,
            arrival_mean=args.arrival_mean,
            time_scale=args.time_scale,
        )
    except KeyboardInterrupt:
        pass
    finally:
        if args.out:
            out.close()
        if record:
            record.close()
