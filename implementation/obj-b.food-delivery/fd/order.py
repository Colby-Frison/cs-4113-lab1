"""One order: metadata in its ID, preparation/delivery waits in tasks 0/1.

Like the matchmaking application, keep the shared three-column CSV intact.
"""
from dataclasses import dataclass
import re

from lib.job import Job

ZONES = ("near", "mid", "far")
WAIT_PRECISION = 3
PATTERN = re.compile(r"^o(\d+)\.r(\d+)\.(near|mid|far)\.i([1-9]\d*)$")


@dataclass(frozen=True)
class Order:
    seq: int
    restaurant: int
    zone: str
    items: int

    @property
    def job_id(self):
        if self.seq < 0 or self.restaurant < 0 or self.zone not in ZONES or self.items < 1:
            raise ValueError("invalid order attributes")
        return f"o{self.seq}.r{self.restaurant}.{self.zone}.i{self.items}"


def validate_order(job: Job) -> Order:
    match = PATTERN.fullmatch(job.job_id)
    if not match:
        raise ValueError(f"invalid food-delivery order id: {job.job_id}")
    if [task.tid for task in job.tasks] != [0, 1]:
        raise ValueError("order must have preparation task 0 and delivery task 1, in order")
    seq, restaurant, zone, items = match.groups()
    return Order(int(seq), int(restaurant), zone, int(items))
