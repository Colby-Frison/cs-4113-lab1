"""One server process for every logical queue in a hosted study.

``source_edge`` on the request is the queue id that sent the job. The visit
sleeps that stage's task, then submits the original row back to the queue
host with ``dest_qid`` set, or appends a sink file. A per-queue semaphore
caps in-service jobs at that queue's server count.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import threading
import time
from concurrent import futures
from pathlib import Path

MM_ROOT = Path(__file__).resolve().parents[2]
OBJ_A = MM_ROOT.parent / "obj-a"
for _entry in (str(MM_ROOT), str(OBJ_A)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from lib.grpc_spec import parse_dial, parse_listen  # noqa: E402
from lib.logsetup import configure_logging  # noqa: E402
from lib.server import serve_job  # noqa: E402
from mm.stages import STAGES, choose_ready, search_qid_for_ticket  # noqa: E402
from mm.ticket import parse_ticket  # noqa: E402

_GRPC_INSTALL = (
    "gRPC edges need grpcio and protobuf. "
    "pip install -r implementation/obj-a/requirements.txt"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Host every matchmaking stage server in one process.")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--listen", required=True, help="interface:port,workers")
    parser.add_argument("--queue", required=True, help="Queue host address host:port")
    parser.add_argument("--matched", required=True)
    parser.add_argument("--failed", required=True)
    parser.add_argument("--logfile", required=True)
    parser.add_argument("--state-file", required=True)
    args = parser.parse_args(argv)

    try:
        import grpc
        from lib.grpc_runtime import job_from_message, submit_job
        from lib.proto_gen import job_pb2, queue_to_server_pb2_grpc
    except ImportError as exc:
        raise SystemExit(_GRPC_INSTALL) from exc

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    by_qid = {row["qid"]: row for row in spec["queues"]}
    listen = parse_listen(args.listen)
    queue_dial = parse_dial(f"{args.queue},1", caller="server")
    matched = Path(args.matched)
    failed = Path(args.failed)
    outfiles = [str(matched), str(failed)]
    pool = max(1, sum(row["workers"] for row in spec["queues"]))

    configure_logging(args.logfile, "sv")
    logger = logging.getLogger("server-host")
    state: dict[str, int] = {}
    state_lock = threading.Lock()
    semaphores = {qid: threading.BoundedSemaphore(row["workers"]) for qid, row in by_qid.items()}
    tls = threading.local()
    rng = random.Random(int(spec["seed"]))
    rng_lock = threading.Lock()

    def submit_fn(edge, job, qid):
        return submit_job(edge, job, qid, dest_qid=tls.dest_qid, logger=logger)

    def next_hop(queue: dict, job, chooser: random.Random) -> tuple[str, str]:
        server = queue["server"]
        if server in ("validate", "rating", "search"):
            return "qid", queue["next"]
        if server == "route":
            ticket = parse_ticket(job.job_id)
            if ticket is None:
                return "file", ""
            return "qid", search_qid_for_ticket(
                ticket,
                replicas=int(spec["replicas"]),
                search_base=int(spec["search_base"]),
                regions=tuple(spec["regions"]),
                modes=tuple(spec["modes"]),
                bands=tuple(spec["bands"]),
                single=bool(spec.get("single", False)),
            )
        if server == "ready":
            visits = state.get(job.job_id, 1)
            decision = choose_ready(
                visits,
                chooser,
                float(spec["ready_fail_p"]),
                int(spec["max_ready_visits"]),
                "pass",
                "retry",
                "fail",
            )
            if decision == "pass":
                return "qid", spec["allocate_qid"]
            if decision == "fail":
                return "file", str(failed)
            ticket = parse_ticket(job.job_id)
            if ticket is None:
                return "file", ""
            return "qid", search_qid_for_ticket(
                ticket,
                replicas=int(spec["replicas"]),
                search_base=int(spec["search_base"]),
                regions=tuple(spec["regions"]),
                modes=tuple(spec["modes"]),
                bands=tuple(spec["bands"]),
                single=bool(spec.get("single", False)),
            )
        if server == "allocate":
            return "file", str(matched)
        return "file", ""

    def edge_chooser(job, _outfiles, grpc_edges, chooser):
        queue = by_qid[tls.source]
        kind, dest = next_hop(queue, job, chooser)
        if kind == "file":
            tls.dest_qid = ""
            return dest or None
        tls.dest_qid = dest
        return grpc_edges[0].address

    def visit(job, source: str) -> None:
        queue = by_qid[source]
        ticket = parse_ticket(job.job_id)
        needed = {STAGES[queue["server"]]}
        present = {task.tid for task in job.tasks}
        if ticket is None or not needed <= present:
            logger.error("skipping %s on %s", job.job_id, source)
            return
        tls.source = source
        ready = queue["server"] == "ready"
        semaphores[source].acquire()
        try:
            with rng_lock:
                roll = random.Random(rng.randrange(2**31))
            serve_job(
                job,
                outfiles,
                source,
                0,
                logger,
                state=state if ready else None,
                writers=writers,
                grpc_edges=[queue_dial],
                submit_fn=submit_fn,
                state_lock=state_lock if ready else None,
                only_tids=needed,
                edge_chooser=edge_chooser,
                rng=roll,
            )
        finally:
            semaphores[source].release()

    class _Visit(queue_to_server_pb2_grpc.QueueToServerServicer):
        def Submit(self, request, context):  # noqa: N802 - gRPC method name
            job = job_from_message(request.job)
            source = request.source_edge
            if source not in by_qid:
                logger.error("unknown source queue %s", source)
            else:
                visit(job, source)
            return job_pb2.SubmitJobResponse(accepted=True)

    matched.parent.mkdir(parents=True, exist_ok=True)
    failed.parent.mkdir(parents=True, exist_ok=True)
    with matched.open("a", encoding="utf-8", newline="\n") as matched_handle, failed.open(
        "a", encoding="utf-8", newline="\n"
    ) as failed_handle:
        writers = [matched_handle, failed_handle]
        server = grpc.server(futures.ThreadPoolExecutor(max_workers=max(listen.workers, pool)))
        queue_to_server_pb2_grpc.add_QueueToServerServicer_to_server(_Visit(), server)
        bound = server.add_insecure_port(listen.address)
        if bound == 0:
            raise SystemExit(f"cannot bind {listen.address}")
        server.start()
        logger.info("gRPC listen %s:%s workers %s", listen.host, bound, max(listen.workers, pool))
        try:
            while True:
                time.sleep(0.2)
        except KeyboardInterrupt:
            logger.info("stopping server host")
        finally:
            server.stop(1)
            _save_state(Path(args.state_file), state)
    return 0


def _save_state(path: Path, state: dict[str, int]) -> None:
    lines = [f"{job_id} {visits}" for job_id, visits in sorted(state.items())]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
