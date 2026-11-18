"""A packet routing router with a firewall in its visit.

The monolith and the validate stage check the access list inside the visit, after
the tasks and before ``EXITS-SERVER``. The check costs ``rule_wait`` per rule
checked. A denied packet is dropped with ``ERROR FIREWALL-DENY`` after the visit.
Lookup and forward work as in packet routing.
"""

from __future__ import annotations

from lib.events import format_event
from lib.job import Job

from pk.router import VALIDATE_TID, Router

from fw.firewall import Firewall


class FirewallRouter(Router):
    def __init__(self, *args, firewall: Firewall, rule_wait: float = 0.0, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.firewall = firewall
        self.rule_wait = rule_wait

    def _admit(self, job: Job, tid: int | None) -> bool:
        if tid not in (None, VALIDATE_TID):
            return True
        allowed, checked = self.firewall.check(job.dst_ip)
        self.sleep_fn(checked * self.rule_wait)
        return allowed

    def _reject(self, job: Job) -> None:
        self.log.info("%s", format_event(job.job_id, self.qid, "ERROR", "FIREWALL-DENY"))
