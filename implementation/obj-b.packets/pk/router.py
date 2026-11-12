"""The visits a router's server functions run.

``monolith`` does a router's whole job in one visit. ``validate``, ``lookup``,
and ``forward`` are the same job split into three visits, each behind its own
queue. A packet's tasks name the work: task 0 validates, task 1 looks up, and
task 2 forwards. The monolith sleeps all three, and a stage sleeps only its own.

The two forms make the same decisions in the same order. They differ in one
way: the monolith spends its whole visit before deciding, while a stage that
drops a packet saves the work of the stages after it.

A visit returns only after the packet has been handed on, so a server thread
stays busy for the whole visit.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from typing import Callable, TextIO

from lib.events import (
    depart_via,
    enters_server,
    error_no_out_edge,
    error_ttl_expired,
    exits_server,
    task_end,
    task_start,
)
from lib.grpc_spec import parse_dial
from lib.job import Job

from pk.routing import LOCAL, QueueAddress, RoutingTable

VALIDATE_TID = 0
LOOKUP_TID = 1
FORWARD_TID = 2

STAGES = {"validate": VALIDATE_TID, "lookup": LOOKUP_TID, "forward": FORWARD_TID}

SubmitFn = Callable[..., str]


class Sink:
    """Appends delivered rows. gRPC workers share one file handle."""

    def __init__(self, handle: TextIO, label: str) -> None:
        self.label = label
        self._handle = handle
        self._lock = threading.Lock()

    def write(self, raw_line: str) -> None:
        with self._lock:
            self._handle.write(raw_line + "\n")
            self._handle.flush()


class Router:
    """One server function process. Holds what its visits need."""

    def __init__(
        self,
        qid: str,
        server_id: int,
        log: logging.Logger,
        *,
        addresses: dict[str, QueueAddress],
        table: RoutingTable | None = None,
        sink: Sink | None = None,
        next_qid: str | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        submit_fn: SubmitFn | None = None,
    ) -> None:
        self.qid = qid
        self.server_id = server_id
        self.log = log
        self.addresses = addresses
        self.table = table
        self.sink = sink
        self.next_qid = next_qid
        self.sleep_fn = sleep_fn
        self.submit_fn = submit_fn

    def monolith(self, job: Job) -> None:
        self._work(job, None)
        if not self._ttl_is_valid(job):
            return
        next_hop = self._lookup(job)
        if next_hop is not None:
            self._hand_on(job, next_hop)

    def validate(self, job: Job) -> None:
        self._work(job, VALIDATE_TID)
        if self._ttl_is_valid(job):
            self._send(job, self.next_qid)

    def lookup(self, job: Job) -> None:
        self._work(job, LOOKUP_TID)
        next_hop = self._lookup(job)
        if next_hop is not None:
            self._send(replace(job, next_hop=next_hop), self.next_qid)

    def forward(self, job: Job) -> None:
        self._work(job, FORWARD_TID)
        self._hand_on(job, job.next_hop)

    def _work(self, job: Job, tid: int | None) -> None:
        self.log.info("%s", enters_server(job.job_id, self.qid, self.server_id))
        for task in job.tasks:
            if tid is not None and task.tid != tid:
                continue
            self.log.info("%s", task_start(job.job_id, self.qid, task.tid, task.wait_seconds))
            self.sleep_fn(task.wait_seconds)
            self.log.info("%s", task_end(job.job_id, self.qid, task.tid, task.wait_seconds))
        self.log.info("%s", exits_server(job.job_id, self.qid, self.server_id))

    def _ttl_is_valid(self, job: Job) -> bool:
        if job.ttl > 0:
            return True
        self.log.info("%s", error_ttl_expired(job.job_id, self.qid))
        return False

    def _lookup(self, job: Job) -> str | None:
        next_hop = self.table.lookup(job.dst_ip)
        if next_hop is None:
            self.log.info("%s", error_no_out_edge(job.job_id, self.qid))
        return next_hop

    def _hand_on(self, job: Job, next_hop: str) -> None:
        """Deliver here, or reduce the TTL and send to the next router."""
        if next_hop == LOCAL:
            self.sink.write(job.raw_line)
            self.log.info("%s", depart_via(job.job_id, self.qid, self.sink.label))
            return
        ttl = job.ttl - 1
        if ttl <= 0:
            self.log.info("%s", error_ttl_expired(job.job_id, self.qid))
            return
        self._send(replace(job, ttl=ttl, next_hop=""), next_hop)

    def _send(self, job: Job, dest_qid: str) -> None:
        address = self.addresses.get(dest_qid)
        if address is None:
            self.log.error("no address for %s", dest_qid)
            self.log.info("%s", error_no_out_edge(job.job_id, self.qid))
            return
        dial = parse_dial(f"{address.host}:{address.queue_port},1", caller="server")
        outcome = self._submit(dial, job)
        # A transport failure has already logged an error. The job did not leave.
        if outcome != "failed":
            self.log.info("%s", depart_via(job.job_id, self.qid, dial.address))

    def _submit(self, dial, job: Job) -> str:
        if self.submit_fn is not None:
            return self.submit_fn(dial, job, self.qid)
        from lib.grpc_runtime import submit_job

        return submit_job(dial, job, self.qid, logger=self.log)
