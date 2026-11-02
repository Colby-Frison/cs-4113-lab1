"""One queue process that holds every logical queue for a large study.

Each qid has its own buffer. One listen port accepts ``Submit`` and uses
``dest_qid`` to choose the buffer. An empty ``dest_qid`` is the admit queue.
Sender threads pull each buffer and hand the job to the server host.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
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

from lib.events import depart_via  # noqa: E402
from lib.grpc_spec import parse_dial, parse_listen  # noqa: E402
from lib.job import JobParseError, is_header, parse_job_line  # noqa: E402
from lib.logsetup import configure_logging  # noqa: E402

_GRPC_INSTALL = (
    "gRPC edges need grpcio and protobuf. "
    "pip install -r implementation/obj-a/requirements.txt"
)


def _admit_job():
    """Load Objective A's admit helper without putting ``queue/`` on the import path."""
    path = OBJ_A / "queue" / "queue.py"
    spec = importlib.util.spec_from_file_location("mm_obj_a_queue", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.admit_job, module.Queue


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Host many matchmaking queues in one process.")
    parser.add_argument("--spec", required=True, help="Host spec JSON from expand.write_host_spec.")
    parser.add_argument("--listen", required=True, help="interface:port,workers")
    parser.add_argument("--server", required=True, help="Server host address host:port")
    parser.add_argument("--pipe", required=True, help="Named pipe the generator writes. Admit reads it.")
    parser.add_argument("--logfile", required=True)
    args = parser.parse_args(argv)

    try:
        import grpc
        from lib.grpc_runtime import job_from_message, submit_job
        from lib.proto_gen import job_pb2, server_to_queue_pb2_grpc
    except ImportError as exc:
        raise SystemExit(_GRPC_INSTALL) from exc

    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    by_qid = {row["qid"]: row for row in spec["queues"]}
    admit_qid = spec["admit_qid"]
    admit_job, queue_type = _admit_job()
    buffers = {qid: queue_type(maxsize=row["qsize"]) for qid, row in by_qid.items()}

    configure_logging(args.logfile, "Q")
    logger = logging.getLogger("queue-host")
    listen = parse_listen(args.listen)
    dial = parse_dial(f"{args.server},1", caller="queue")

    class _Admit(server_to_queue_pb2_grpc.ServerToQueueServicer):
        def Submit(self, request, context):  # noqa: N802 - gRPC method name
            job = job_from_message(request.job)
            dest = request.dest_qid or admit_qid
            source = request.source_edge or "grpc"
            if dest not in buffers:
                logger.error("no buffer for dest_qid %s", dest)
                return job_pb2.SubmitJobResponse(accepted=False)
            accepted = admit_job(buffers[dest], job, dest, source, logger)
            return job_pb2.SubmitJobResponse(accepted=accepted)

    def send(qid: str) -> None:
        buffer = buffers[qid]
        while True:
            item = buffer.get()
            if item is None:
                buffer.put(item)
                break
            outcome = submit_job(dial, item, qid, logger=logger)
            if outcome != "failed":
                logger.info("%s", depart_via(item.job_id, qid, dial.address))

    def read_pipe() -> None:
        logger.info("opening job pipe %s", args.pipe)
        try:
            with open(args.pipe, encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip() or is_header(line):
                        continue
                    try:
                        job = parse_job_line(line)
                    except JobParseError as exc:
                        logger.error("skipping unreadable job line (%s)", exc)
                        continue
                    admit_job(buffers[admit_qid], job, admit_qid, args.pipe, logger)
        except OSError as exc:
            logger.error("job pipe closed: %s", exc)

    threading.Thread(target=read_pipe, name="admit-pipe", daemon=True).start()
    for qid, row in by_qid.items():
        for index in range(row["workers"]):
            threading.Thread(target=send, args=(qid,), name=f"send-{qid}-{index}", daemon=True).start()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=listen.workers))
    server_to_queue_pb2_grpc.add_ServerToQueueServicer_to_server(_Admit(), server)
    bound = server.add_insecure_port(listen.address)
    if bound == 0:
        raise SystemExit(f"cannot bind {listen.address}")
    server.start()
    logger.info("gRPC listen %s:%s workers %s", listen.host, bound, listen.workers)
    try:
        while True:
            time.sleep(0.2)
    except KeyboardInterrupt:
        logger.info("stopping queue host")
    finally:
        server.stop(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
