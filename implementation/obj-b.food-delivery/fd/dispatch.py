"""Stateful pickup queue and courier pool, owned by one dispatcher process.

Admission, assignment, and release share a condition lock. Couriers take the
oldest admitted order only while available; there are no per-courier FIFOs.
"""
import argparse
from collections import deque
import json
import logging
from pathlib import Path
import threading
import time

from fd.services import validate_request
from fd.server import serve
from fd.workload import positive_int
from lib.events import arrive_via, error_queue_full
from lib.job import iter_jobs


def file_logger(path, component):
    logger = logging.Logger(str(path), logging.INFO)
    handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter(f"%(asctime)s - {component} - %(levelname)s - %(message)s"))
    logger.addHandler(handler)
    return logger


class Dispatcher:
    def __init__(self, couriers, capacity, root, sleep=time.sleep):
        if couriers < 1 or capacity < 1:
            raise ValueError("couriers and capacity must be positive")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.capacity = capacity
        self.sleep = sleep
        self.pending = deque()
        self.condition = threading.Condition()
        self.couriers = {i: {"status": "available", "order_id": None, "completed": 0} for i in range(couriers)}
        self.accepted = set()
        self.readers_done = 0
        self.stopping = False
        self.failure = None
        self.log = file_logger(self.root / "q1.log", "Q")
        self.persist()

    def persist(self):
        """Atomic inspection snapshot; not a crash-recovery checkpoint."""
        data = {"couriers": self.couriers, "waiting": [j.job_id for j in self.pending]}
        temporary = self.root / "dispatch-state.tmp"
        temporary.write_text(json.dumps(data, indent=2) + "\n")
        temporary.replace(self.root / "dispatch-state.json")

    def admit(self, job, source):
        validate_request(job)
        with self.condition:
            if job.job_id in self.accepted:
                raise ValueError(f"duplicate dispatch order: {job.job_id}")
            if len(self.pending) >= self.capacity:
                self.log.info("%s", error_queue_full(job.job_id, "q1"))
                return False
            self.accepted.add(job.job_id)
            self.pending.append(job)
            self.log.info("%s", arrive_via(job.job_id, "q1", source))
            self.persist()
            self.condition.notify_all()
            return True

    def fail(self, exc):
        with self.condition:
            self.failure = exc
            self.stopping = True
            self.condition.notify_all()

    def read(self, path):
        try:
            with open(path, encoding="utf-8") as stream:
                for job in iter_jobs(stream):
                    with self.condition:
                        if self.stopping:
                            return
                    self.admit(job, str(path))
        except Exception as exc:
            self.fail(exc)
        finally:
            with self.condition:
                self.readers_done += 1
                self.condition.notify_all()

    def assign_next(self, courier_id):
        """Wait for the oldest pending order and atomically reserve a courier."""
        with self.condition:
            courier = self.couriers[courier_id]
            if courier["status"] != "available":
                raise ValueError(f"courier {courier_id} is not available")
            self.condition.wait_for(lambda: self.pending or self.stopping)
            if self.stopping:
                return None
            # Recheck after waiting, since the condition releases the lock.
            if courier["status"] != "available":
                raise ValueError(f"courier {courier_id} is not available")
            job = self.pending.popleft()
            courier.update(status="busy", order_id=job.job_id)
            self.log.info("dispatch-assigned courier_%s %s", courier_id, job.job_id)
            self.persist()
            return job

    def release(self, courier_id, order_id):
        """Complete only the active assignment, after delivery and output succeed."""
        with self.condition:
            courier = self.couriers[courier_id]
            if courier["status"] != "busy" or courier["order_id"] != order_id:
                raise ValueError(f"courier {courier_id} has no active assignment for {order_id}")
            courier["completed"] += 1
            courier.update(status="available", order_id=None)
            self.log.info("dispatch-released courier_%s %s", courier_id, order_id)
            self.persist()
            self.condition.notify_all()

    def worker(self, courier_id):
        log = file_logger(self.root / f"courier-{courier_id}.log", "sv")
        try:
            destination = self.root / f"sink-{courier_id}.csv"
            with destination.open("w", encoding="utf-8") as writer:
                while True:
                    job = self.assign_next(courier_id)
                    if job is None:
                        return
                    serve(job, "courier", "q1", courier_id, writer, str(destination), log, sleep=self.sleep)
                    self.release(courier_id, job.job_id)
        except Exception as exc:
            try:
                with self.condition:
                    self.couriers[courier_id]["status"] = "failed"
                    self.persist()
            finally:
                self.fail(exc)
        finally:
            for handler in log.handlers:
                handler.close()

    def run(self, sources):
        workers = [threading.Thread(target=self.worker, args=(i,), daemon=True) for i in self.couriers]
        readers = [threading.Thread(target=self.read, args=(p,), daemon=True) for p in sources]
        try:
            for thread in workers + readers:
                thread.start()
            with self.condition:
                self.condition.wait_for(lambda: self.failure is not None or (
                    self.readers_done == len(readers) and not self.pending and
                    all(c["status"] == "available" for c in self.couriers.values())))
                self.stopping = True
                self.condition.notify_all()
            if self.failure is not None:
                raise RuntimeError(f"dispatch failed: {self.failure}") from self.failure
            for thread in workers + readers:
                thread.join()
        finally:
            for handler in self.log.handlers:
                handler.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--couriers", type=positive_int, required=True)
    parser.add_argument("--capacity", type=positive_int, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--infile", action="append", required=True)
    args = parser.parse_args()
    Dispatcher(args.couriers, args.capacity, args.workdir).run(args.infile)
