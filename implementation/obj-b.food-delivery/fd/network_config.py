"""Configuration checks shared by the regional network runners."""

import math


def validate_config(config):
    """Check topology, worker counts, and timing before starting any processes."""
    required = {
        "restaurant_regions",
        "restaurant_weights",
        "kitchen_workers",
        "couriers",
        "kitchen_capacity",
        "pickup_capacity",
        "count",
        "arrival_mean",
        "time_scale",
        "seed",
    }
    if set(config) != required:
        raise ValueError("invalid network configuration keys")
    restaurant_regions = config["restaurant_regions"]
    couriers = config["couriers"]
    kitchen_workers = config["kitchen_workers"]
    demand_weights = config["restaurant_weights"]
    if (
        not restaurant_regions
        or not couriers
        or len(restaurant_regions) != len(kitchen_workers)
        or len(restaurant_regions) != len(demand_weights)
    ):
        raise ValueError("inconsistent restaurant configuration")
    # every pickup region needs at least one restaurant feeding it
    if any(
        type(region) is not int or not 0 <= region < len(couriers) for region in restaurant_regions
    ) or set(restaurant_regions) != set(range(len(couriers))):
        raise ValueError("each region must have restaurants and a valid region ID")
    positive_counts = (
        kitchen_workers
        + couriers
        + [config["kitchen_capacity"], config["pickup_capacity"], config["count"]]
    )
    # exact type checks reject booleans, which Python otherwise treats as integers
    if any(type(count) is not int or count < 1 for count in positive_counts):
        raise ValueError("worker counts/capacities/count must be positive integers")
    if type(config["seed"]) is not int:
        raise ValueError("seed must be integer")
    if any(not math.isfinite(weight) or weight <= 0 for weight in demand_weights):
        raise ValueError("invalid demand weights")
    if any(not math.isfinite(config[k]) or config[k] < 0 for k in ("arrival_mean", "time_scale")):
        raise ValueError("invalid timing configuration")
