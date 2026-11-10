"""Workload jobs, just kept separae from the main simulator code for clarity."""
from dataclasses import dataclass, field

@dataclass
class Job:
    id: str
    arrival: int
    tasks: list
    payload: dict
    findings: list = field(default_factory=list)
    verification: list = field(default_factory=list)
    status: str = 'pending'
    error: str = ''
    prepared: str = ''

def task_parts(task):
    tid, token = task.split(':', 1)
    op, amount = token.split()
    if op != 'W' or int(amount) < 1:
        raise ValueError('expected task of form tid: W N, N>=1')
    return int(tid), token.strip()
