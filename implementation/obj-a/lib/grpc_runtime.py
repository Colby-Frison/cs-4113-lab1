"""gRPC servers and clients for the three Objective A edges.

Imported only when a process is actually given --ingrpc or --outgrpc, so the
file/FIFO path does not need grpcio installed.

A queue listen port serves both ServerToQueue and QueueToQueue. Both admit
into the same buffer. A server function listen port serves QueueToServer and
runs the wait-task visit before returning, so the caller blocks for the sleep.
That is the backpressure: a full visit occupies one server thread and one
queue sender thread. Submit reuses one channel per peer address so a long
run does not open a new TCP port for every job.
"""

from __future__ import annotations

import logging
import threading
from concurrent import futures
from dataclasses import replace
from typing import Callable

import grpc

from lib.grpc_spec import (
    QUEUE_TO_QUEUE,
    QUEUE_TO_SERVER,
    SERVER_TO_QUEUE,
    DialSpec,
    ListenSpec,
)
from lib.job import Job, JobParseError, Task, format_job_line, parse_job_line
from lib.proto_gen import job_pb2, queue_to_queue_pb2_grpc, queue_to_server_pb2_grpc, server_to_queue_pb2_grpc

AdmitFn = Callable[[Job, str], bool]
VisitFn = Callable[[Job], None]

# One channel per peer. A new TCP connection per job leaves the local port in
# TIME_WAIT, and a thousand-ticket pipeline then fails later connects with
# WSAEADDRINUSE (10048) on Windows.
_channels: dict[str, grpc.Channel] = {}
_channel_lock = threading.Lock()


def _cached_channel(address: str) -> grpc.Channel:
    with _channel_lock:
        channel = _channels.get(address)
        if channel is None:
            channel = grpc.insecure_channel(address)
            _channels[address] = channel
        return channel


def _drop_channel(address: str, channel: grpc.Channel) -> None:
    with _channel_lock:
        if _channels.get(address) is channel:
            _channels.pop(address, None)
    channel.close()


class _QueueAdmit(
    server_to_queue_pb2_grpc.ServerToQueueServicer,
    queue_to_queue_pb2_grpc.QueueToQueueServicer,
):
    """Both queue-ingress services share one admit function."""

    def __init__(self, admit: AdmitFn) -> None:
        self._admit = admit

    def Submit(self, request, context):  # noqa: N802 - gRPC method name
        job = job_from_message(request.job)
        source = request.source_edge or "grpc"
        # dest_qid addresses one buffer inside the multi-queue host. This
        # process has one buffer, so the field is ignored.
        accepted = self._admit(job, source)
        return job_pb2.SubmitJobResponse(accepted=accepted)


class _ServerVisit(queue_to_server_pb2_grpc.QueueToServerServicer):
    def __init__(self, visit: VisitFn) -> None:
        self._visit = visit

    def Submit(self, request, context):  # noqa: N802 - gRPC method name
        job = job_from_message(request.job)
        self._visit(job)
        return job_pb2.SubmitJobResponse(accepted=True)


def start_queue_server(listen: ListenSpec, admit: AdmitFn) -> tuple[grpc.Server, int]:
    """Bind a queue ingress port. Returns the server and the port actually bound."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=listen.workers))
    handler = _QueueAdmit(admit)
    server_to_queue_pb2_grpc.add_ServerToQueueServicer_to_server(handler, server)
    queue_to_queue_pb2_grpc.add_QueueToQueueServicer_to_server(handler, server)
    port = _bind(server, listen)
    server.start()
    return server, port


def start_server_func_server(listen: ListenSpec, visit: VisitFn) -> tuple[grpc.Server, int]:
    """Bind a server-function port. Submit runs the visit before it returns."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=listen.workers))
    queue_to_server_pb2_grpc.add_QueueToServerServicer_to_server(_ServerVisit(visit), server)
    port = _bind(server, listen)
    server.start()
    return server, port


def submit_job(
    dial: DialSpec,
    job: Job,
    source_edge: str,
    *,
    logger: logging.Logger | None = None,
    dest_qid: str = "",
) -> str:
    """Send one job. Return ``accepted``, ``rejected``, or ``failed``.

    ``rejected`` means the peer returned accepted=false (the queue logged
    QUEUE-FULL). ``failed`` is a transport error. The caller logs DEPART-VIA
    for accepted and rejected, and skips it for failed. ``dest_qid`` is empty
    for a process that owns one queue.
    """
    channel = _cached_channel(dial.address)
    try:
        stub = _stub(dial.service, channel)
        response = stub.Submit(_request(job, source_edge, dest_qid))
    except grpc.RpcError as exc:
        _drop_channel(dial.address, channel)
        if logger is not None:
            logger.error("gRPC submit to %s failed: %s", dial.address, exc)
        return "failed"
    return "accepted" if response.accepted else "rejected"


def job_from_message(message) -> Job:
    """Prefer the CSV row for the job, and the message for the packet header.

    The row keeps the starting TTL for the whole path, so the current header
    has to come from the message.
    """
    return replace(
        _job_body(message),
        dst_ip=message.dst_ip,
        ttl=message.ttl,
        next_hop=message.next_hop,
    )


def _job_body(message) -> Job:
    if message.raw_line:
        try:
            return parse_job_line(message.raw_line)
        except JobParseError:
            pass
    tasks = tuple(Task(task.tid, task.wait_seconds) for task in message.tasks)
    if not tasks:
        raise ValueError("gRPC job has no tasks and no parseable raw_line")
    raw_line = format_job_line(message.job_id, message.arrival_time, tasks)
    return parse_job_line(raw_line)


def _request(job: Job, source_edge: str, dest_qid: str = ""):
    message = job_pb2.Job(
        job_id=job.job_id,
        arrival_time=job.arrival_time,
        raw_line=job.raw_line,
        tasks=[job_pb2.Task(tid=task.tid, wait_seconds=task.wait_seconds) for task in job.tasks],
        dst_ip=job.dst_ip,
        ttl=job.ttl,
        next_hop=job.next_hop,
    )
    return job_pb2.SubmitJobRequest(job=message, source_edge=source_edge, dest_qid=dest_qid)


def _stub(service: str, channel):
    if service == QUEUE_TO_SERVER:
        return queue_to_server_pb2_grpc.QueueToServerStub(channel)
    if service == SERVER_TO_QUEUE:
        return server_to_queue_pb2_grpc.ServerToQueueStub(channel)
    if service == QUEUE_TO_QUEUE:
        return queue_to_queue_pb2_grpc.QueueToQueueStub(channel)
    raise ValueError(f"unknown gRPC service {service}")


def _bind(server: grpc.Server, listen: ListenSpec) -> int:
    bound = server.add_insecure_port(listen.address)
    if bound == 0:
        raise SystemExit(f"cannot bind gRPC address {listen.address}")
    return bound
