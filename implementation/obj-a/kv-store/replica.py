#!/usr/bin/env python3
"""KV replica process that answers two-phase commit RPCs.

Holds a committed visit map and a pending map keyed by transaction id.
Optional ``--store-kv-to`` rewrites JSON after every successful Commit.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from concurrent import futures
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.kv_store import load_kv, save_kv
from lib.logsetup import configure_logging

logger = logging.getLogger("kv-replica")


class ReplicaState:
    """Committed values plus in-flight prepares."""

    def __init__(self, committed: dict[str, int], store_path: Path | None) -> None:
        self.committed = committed
        self.pending: dict[str, tuple[str, int]] = {}
        self.store_path = store_path
        self.lock = threading.Lock()

    def prepare(self, txn_id: str, key: str, value: int) -> tuple[bool, str]:
        if not txn_id or not key:
            return False, "missing txn_id or key"
        if value < 0:
            return False, "negative value"
        with self.lock:
            if txn_id in self.pending:
                return False, "duplicate txn"
            self.pending[txn_id] = (key, value)
            return True, ""

    def commit(self, txn_id: str) -> bool:
        with self.lock:
            staged = self.pending.pop(txn_id, None)
            if staged is None:
                return False
            key, value = staged
            self.committed[key] = value
            if self.store_path is not None:
                save_kv(self.store_path, self.committed)
            return True

    def abort(self, txn_id: str) -> bool:
        with self.lock:
            self.pending.pop(txn_id, None)
            return True

    def get(self, key: str) -> tuple[bool, int]:
        with self.lock:
            if key not in self.committed:
                return False, 0
            return True, self.committed[key]


def _servicer(state: ReplicaState):
    from lib.proto_gen import kv_replica_pb2, kv_replica_pb2_grpc

    class Servicer(kv_replica_pb2_grpc.KvReplicaServicer):
        def Prepare(self, request, context):  # noqa: N802
            ok, reason = state.prepare(request.txn_id, request.key, int(request.value))
            logger.info(
                "prepare txn %s key %s value %s vote %s",
                request.txn_id,
                request.key,
                request.value,
                "yes" if ok else "no",
            )
            return kv_replica_pb2.KvPrepareResponse(vote_yes=ok, reason=reason)

        def Commit(self, request, context):  # noqa: N802
            ok = state.commit(request.txn_id)
            logger.info("commit txn %s ok %s", request.txn_id, ok)
            return kv_replica_pb2.KvCommitResponse(ok=ok)

        def Abort(self, request, context):  # noqa: N802
            ok = state.abort(request.txn_id)
            logger.info("abort txn %s ok %s", request.txn_id, ok)
            return kv_replica_pb2.KvAbortResponse(ok=ok)

        def Get(self, request, context):  # noqa: N802
            found, value = state.get(request.key)
            return kv_replica_pb2.KvGetResponse(found=found, value=value)

    return Servicer()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kv-replica", description="KV replica for 2PC.")
    parser.add_argument(
        "--listen",
        required=True,
        help="interface:port,workers (example 0.0.0.0:51000,2)",
    )
    parser.add_argument("--load-kv-from", help="JSON visit map loaded at start")
    parser.add_argument("--store-kv-to", help="JSON visit map rewritten on each commit")
    parser.add_argument("--logfile", help="Log path (default: stderr)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.logfile, "kv")
    try:
        import grpc
    except ImportError as exc:
        raise SystemExit(
            "kv-replica needs grpcio. pip install -r implementation/obj-a/requirements.txt"
        ) from exc
    from lib.grpc_spec import parse_listen
    from lib.proto_gen import kv_replica_pb2_grpc

    listen = parse_listen(args.listen)
    committed: dict[str, int] = {}
    if args.load_kv_from:
        committed = load_kv(Path(args.load_kv_from))
        logger.info("load-kv-from %s entries %s", args.load_kv_from, len(committed))
    store_path = Path(args.store_kv_to) if args.store_kv_to else None
    state = ReplicaState(committed, store_path)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=listen.workers))
    kv_replica_pb2_grpc.add_KvReplicaServicer_to_server(_servicer(state), server)
    bound = server.add_insecure_port(f"{listen.host}:{listen.port}")
    if bound == 0:
        raise SystemExit(f"could not bind {listen.host}:{listen.port}")
    server.start()
    logger.info("Started")
    logger.info("gRPC listen %s:%s workers %s; Ctrl-C to stop", listen.host, bound, listen.workers)
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("stopping")
    finally:
        server.stop(1)
        if store_path is not None:
            save_kv(store_path, state.committed)
            logger.info("store-kv-to %s entries %s", store_path, len(state.committed))
        for key, value in sorted(state.committed.items()):
            logger.info("state-visits %s %s", key, value)
        logger.info("Finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
