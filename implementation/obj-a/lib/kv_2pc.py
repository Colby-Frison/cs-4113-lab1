"""Two-phase commit client for KV replica processes.

The stateful server function keeps a local visit map and, when
``--kv-replicas`` is set, stages each increment on every replica before
committing. A No vote or RPC failure aborts the transaction and leaves the
local map unchanged.
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Callable

import grpc

from lib.proto_gen import kv_replica_pb2, kv_replica_pb2_grpc

VisitCountFn = Callable[[dict[str, int], str, threading.Lock | None], None]


class TwoPhaseCoordinator:
    """Run Prepare then Commit/Abort against a fixed list of replica addresses."""

    def __init__(
        self,
        addresses: list[str],
        *,
        timeout_sec: float = 2.0,
        logger: logging.Logger | None = None,
    ) -> None:
        if len(addresses) < 2:
            raise ValueError("need at least two replica addresses")
        if timeout_sec <= 0:
            raise ValueError("timeout_sec must be > 0")
        self._addresses = list(addresses)
        self._timeout = timeout_sec
        self._log = logger or logging.getLogger("kv-2pc")
        self._channels: dict[str, grpc.Channel] = {}
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            for channel in self._channels.values():
                channel.close()
            self._channels.clear()

    def make_visit_count_fn(self) -> VisitCountFn:
        """Return a ``visit_count_fn`` suitable for ``serve_job``."""

        def count(state: dict[str, int], job_id: str, state_lock: threading.Lock | None) -> None:
            if state_lock is None:
                self.increment(state, job_id)
                return
            with state_lock:
                self.increment(state, job_id)

        return count

    def increment(self, state: dict[str, int], key: str) -> int | None:
        """2PC-increment ``key``. Return the new count, or None on abort."""
        new_value = state.get(key, 0) + 1
        txn_id = uuid.uuid4().hex
        self._log.info("kv-prepare %s key %s value %s", txn_id, key, new_value)
        votes = []
        for address in self._addresses:
            try:
                votes.append(self._prepare(address, txn_id, key, new_value))
            except grpc.RpcError as exc:
                self._log.info("kv-abort %s reason prepare-rpc %s", txn_id, address)
                self._abort_all(txn_id)
                self._log.error("kv prepare failed on %s: %s", address, exc)
                return None
        if not all(votes):
            self._log.info("kv-abort %s reason vote-no", txn_id)
            self._abort_all(txn_id)
            return None
        for address in self._addresses:
            try:
                if not self._commit(address, txn_id):
                    self._log.info("kv-abort %s reason commit-no %s", txn_id, address)
                    self._abort_all(txn_id)
                    return None
            except grpc.RpcError as exc:
                self._log.info("kv-abort %s reason commit-rpc %s", txn_id, address)
                self._abort_all(txn_id)
                self._log.error("kv commit failed on %s: %s", address, exc)
                return None
        state[key] = new_value
        self._log.info("kv-commit %s key %s value %s", txn_id, key, new_value)
        return new_value

    def _stub(self, address: str) -> kv_replica_pb2_grpc.KvReplicaStub:
        with self._lock:
            channel = self._channels.get(address)
            if channel is None:
                channel = grpc.insecure_channel(address)
                self._channels[address] = channel
        return kv_replica_pb2_grpc.KvReplicaStub(channel)

    def _prepare(self, address: str, txn_id: str, key: str, value: int) -> bool:
        response = self._stub(address).Prepare(
            kv_replica_pb2.KvPrepareRequest(txn_id=txn_id, key=key, value=value),
            timeout=self._timeout,
        )
        return bool(response.vote_yes)

    def _commit(self, address: str, txn_id: str) -> bool:
        response = self._stub(address).Commit(
            kv_replica_pb2.KvCommitRequest(txn_id=txn_id),
            timeout=self._timeout,
        )
        return bool(response.ok)

    def _abort(self, address: str, txn_id: str) -> None:
        try:
            self._stub(address).Abort(
                kv_replica_pb2.KvAbortRequest(txn_id=txn_id),
                timeout=self._timeout,
            )
        except grpc.RpcError:
            return

    def _abort_all(self, txn_id: str) -> None:
        for address in self._addresses:
            self._abort(address, txn_id)
