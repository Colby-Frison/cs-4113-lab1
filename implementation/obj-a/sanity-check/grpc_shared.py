"""Memory-bounded real gRPC transport for many independent logical wait queues."""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import grpc
from lib.events import arrive_via, depart_via
from lib.grpc_runtime import _request, job_from_message
from lib.job import iter_jobs
from lib.proto_gen import job_pb2, queue_to_server_pb2_grpc
from lib.server import serve_job


class AppendOnceHandler(logging.Handler):
    """Write messages without keeping thousands of file descriptors open."""
    def __init__(self, path: Path):
        super().__init__()
        self.path = path

    def emit(self, record):
        with self.lock:
            with self.path.open('a', encoding='utf-8') as stream:
                stream.write(self.format(record) + '\n')


def per_queue_logger(path: Path, prefix: str) -> logging.Logger:
    log = logging.getLogger(f'{prefix}:{path}')
    log.setLevel(logging.INFO)
    log.propagate = False
    if not log.handlers:
        handler = AppendOnceHandler(path)
        handler.setFormatter(logging.Formatter(f'%(asctime)s - {prefix}_pid{os.getpid()}@{socket.gethostname()} - %(levelname)s - %(message)s'))
        log.addHandler(handler)
    return log


def run_grpc_shared(run_dir: Path, servers: dict[int,int], qsize: int, jobs_per_queue: int, wait_sec: float, concurrency: int = 64) -> None:
    """Keep all logical queues alive, but share Python process and gRPC thread pool."""
    n = len(servers)
    # A single gRPC listener handles the whole run. Each RPC carries a queue ID
    # in source_edge and retains a separate per-queue service capacity limit.
    # Global thread cap protects an 8 GiB VM, so it can become an additional
    # bottleneck relative to the one-process-per-queue baseline.
    pool_size = max(1, concurrency)
    semaphores = [threading.BoundedSemaphore(servers[i]) for i in range(n)]
    queue_logs = [per_queue_logger(run_dir / f'q{i}.log', 'Q') for i in range(n)]
    server_logs = [per_queue_logger(run_dir / f'sf-q{i}.log', 'sv') for i in range(n)]
    for i, log in enumerate(queue_logs):
        log.info('Started')
        log.info('qid q%s', i)
        log.info('max-q-size %s', qsize)
    for i, log in enumerate(server_logs):
        log.info('Started')
        log.info('qid q%s server-id 0', i)

    class Dispatch(queue_to_server_pb2_grpc.QueueToServerServicer):
        def Submit(self, request, context):
            try:
                qid = request.source_edge
                if not qid.startswith('q') or not qid[1:].isdigit():
                    context.abort(grpc.StatusCode.INVALID_ARGUMENT, 'missing queue ID')
                index = int(qid[1:])
                if index >= n:
                    context.abort(grpc.StatusCode.INVALID_ARGUMENT, 'queue ID out of range')
                job = job_from_message(request.job)
                with semaphores[index]:
                    serve_job(job, [str(run_dir / f'sink-{qid}.csv')], qid, 0, server_logs[index])
                return job_pb2.SubmitJobResponse(accepted=True)
            except grpc.RpcError:
                raise
            except Exception as exc:
                context.abort(grpc.StatusCode.INTERNAL, str(exc))

    server = grpc.server(ThreadPoolExecutor(max_workers=pool_size))
    queue_to_server_pb2_grpc.add_QueueToServerServicer_to_server(Dispatch(), server)
    port = server.add_insecure_port('127.0.0.1:0')
    if not port:
        raise RuntimeError('gRPC failed to bind')
    server.start()
    target = f'127.0.0.1:{port}'
    print(f'  concurrent grpc queues {n}, shared RPC workers {pool_size}, target {target}', flush=True)
    # One queue thread per qid; jobs never all reside in a Python list.
    # A single shared connection is supported by grpcio and avoids creating
    # a new channel for each of the potentially 102400 jobs.
    channel = grpc.insecure_channel(target)
    stub = queue_to_server_pb2_grpc.QueueToServerStub(channel)
    failed = []
    completed = [0] * n
    def run_queue(i):
        qid = f'q{i}'
        qlog = queue_logs[i]
        filename = run_dir / f'jobs-{qid}.csv'
        try:
            with filename.open(encoding='utf-8') as stream:
                for job in iter_jobs(stream):
                    qlog.info('%s', arrive_via(job.job_id, qid, str(filename)))
                    qlog.info('metric-q-size 1')
                    response = stub.Submit(_request(job, qid), timeout=300)
                    if not response.accepted:
                        raise RuntimeError(f'{qid}: server rejected job {job.job_id}')
                    qlog.info('%s', depart_via(job.job_id, qid, target))
                    qlog.info('metric-q-size 0')
                    completed[i] += 1
        except Exception as exc:
            failed.append(f'{qid}: {exc}')
        finally:
            qlog.info('Finished')

    try:
        with ThreadPoolExecutor(max_workers=min(n, 128)) as workers:
            futures = [workers.submit(run_queue, i) for i in range(n)]
            for future in as_completed(futures):
                future.result()
        if failed:
            raise RuntimeError('gRPC queue failures: ' + '; '.join(failed[:8]))
        if sum(completed) != n * jobs_per_queue:
            raise RuntimeError(f'delivered {sum(completed)}/{n * jobs_per_queue}')
    finally:
        channel.close()
        server.stop(2).wait(3)
        for log in server_logs:
            log.info('Finished')
        for log in queue_logs + server_logs:
            for handler in list(log.handlers):
                handler.close()
                log.removeHandler(handler)
