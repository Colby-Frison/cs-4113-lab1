"""Containerized queue + function services for HomeKey Objective C.

Only imports the existing application code, no modifications to Objective B.
"""
import argparse
import csv
import dataclasses
import fcntl
import json
import os
from pathlib import Path
import queue
import random
import threading
import time

import grpc

from job import Job
from server_function import prepare, scan, review, monolithic, report

DATA = Path(os.environ.get('HOMEKEY_INPUTS', '/work/inputs'))
OUT = Path(os.environ.get('HOMEKEY_OUTPUTS', '/work/outputs'))
IPC = Path(os.environ.get('HOMEKEY_IPC', '/work/ipc'))
OPTIONS = [('grpc.max_send_message_length', 33554432), ('grpc.max_receive_message_length', 33554432)]

def encode(job):
    return dataclasses.asdict(job)

def decode(obj):
    return Job(**obj)

def append(name, record):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    with path.open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(record, separators=(',', ':')) + '\n')
        f.flush()
        fcntl.flock(f, fcntl.LOCK_UN)

def fifo_path(name):
    IPC.mkdir(parents=True, exist_ok=True)
    path = IPC / name
    try:
        os.mkfifo(path, 0o666)
    except FileExistsError:
        pass
    return path

def fifo_send(name, obj):
    path = fifo_path(name)
    line = (json.dumps(obj, separators=(',', ':')) + '\n').encode()
    lock = IPC / (name + '.lock')
    with lock.open('a') as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        fd = os.open(path, os.O_WRONLY)
        try:
            remaining = memoryview(line)
            while remaining:
                remaining = remaining[os.write(fd, remaining):]
        finally:
            os.close(fd)
            fcntl.flock(lockfile, fcntl.LOCK_UN)

def fifo_read(name):
    # RDWR keeps a reader open and avoids startup ordering deadlocks on Linux
    fd = os.open(fifo_path(name), os.O_RDWR)
    with os.fdopen(fd, 'r') as pipe:
        while True:
            line = pipe.readline()
            if line:
                yield json.loads(line)

def grpc_send(addr, obj, timeout=120):
    payload = json.dumps(obj, separators=(',', ':')).encode()
    channel = grpc.insecure_channel(addr, options=OPTIONS)
    call = channel.unary_unary('/homekey.Transport/Send')
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                result = json.loads(call(payload, timeout=10).decode())
                if result.get('error'):
                    raise RuntimeError(result['error'])
                return result
            except grpc.RpcError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)
    finally:
        channel.close()

def grpc_serve(port, handler, workers=8):
    from concurrent.futures import ThreadPoolExecutor
    server = grpc.server(ThreadPoolExecutor(max_workers=max(4, workers)), options=OPTIONS)

    def method(raw, context):
        try:
            return json.dumps(handler(json.loads(raw.decode()))).encode()
        except Exception as exc:
            return json.dumps({'error': str(exc)}).encode()

    generic = grpc.method_handlers_generic_handler(
        'homekey.Transport',
        {'Send': grpc.unary_unary_rpc_method_handler(method, request_deserializer=lambda b: b, response_serializer=lambda b: b)}
    )
    server.add_generic_rpc_handlers((generic,))
    server.add_insecure_port('0.0.0.0:' + str(port))
    server.start()
    server.wait_for_termination()

def stage_execute(stage, job):
    if job.error:
        return job
    if stage == 'Prepare':
        return prepare(job, DATA)
    if stage == 'Scan':
        return scan(job)
    if stage == 'Review':
        return review(job)
    if stage == 'Monolithic':
        return monolithic(job, DATA)
    if stage in ('Verify', 'VerifyReport'):
        # Objective C deployment runs without hardware
        if job.findings:
            job.verification = [dict(f, status='needs_target_specific_test') for f in job.findings]
        if stage == 'VerifyReport':
            return report(job)
        return job
    if stage == 'Report':
        return report(job)
    raise ValueError('Unknown stage ' + stage)

def function_service(args):
    def handle(obj):
        job = decode(obj['job'])
        try:
            job = stage_execute(args.stage, job)
        except Exception as exc:
            job.status = 'analysis_error'
            job.error = str(exc)
        return {'job': encode(job)}

    if args.transport == 'grpc':
        grpc_serve(50052, handle, args.slots + 2)
    else:
        def slot_loop(slot):
            for message in fifo_read(f'req-{args.queue}-{slot}'):
                answer = handle(message)
                fifo_send(f'resp-{args.queue}-{slot}', answer)
        threads = [threading.Thread(target=slot_loop, args=(i,), daemon=True) for i in range(args.slots)]
        for thread in threads:
            thread.start()
        while True:
            time.sleep(3600)

def queue_service(args):
    pending = queue.Queue(maxsize=args.capacity)
    edges = json.loads(args.edges)
    route_lock = threading.Lock()
    cursor = 0

    def receive(obj):
        try:
            pending.put_nowait(obj['job'])
            append('events.jsonl', {'at': time.time(), 'queue': args.queue, 'event': 'arrive', 'job_id': obj['job']['id']})
        except queue.Full:
            append('dropped.jsonl', {'job_id': obj['job']['id'], 'queue': args.queue, 'reason': 'queue_full'})
        return {'ok': True}

    def process(slot):
        nonlocal cursor
        while True:
            source = pending.get()
            job = decode(source)
            begin = time.perf_counter()
            append('events.jsonl', {'at': time.time(), 'queue': args.queue, 'event': 'start', 'job_id': job.id, 'slot': slot})
            try:
                if args.transport == 'grpc':
                    result = grpc_send(f'function-{args.queue}:50052', {'job': encode(job)})
                else:
                    fifo_send(f'req-{args.queue}-{slot}', {'job': encode(job)})
                    result = next(fifo_read(f'resp-{args.queue}-{slot}'))
                job = decode(result['job'])
            except Exception as exc:
                job.status = 'analysis_error'
                job.error = str(exc)
            if job.tasks:
                job.tasks.pop(0)
            append('events.jsonl', {'at': time.time(), 'queue': args.queue, 'event': 'finish', 'job_id': job.id,
                                    'slot': slot, 'seconds': time.perf_counter() - begin})
            if edges:
                with route_lock:
                    dest = edges[cursor % len(edges)]
                    cursor += 1
                try:
                    if args.transport == 'grpc':
                        grpc_send(f'queue-{dest}:50051', {'job': encode(job)})
                    else:
                        fifo_send(f'in-{dest}', {'job': encode(job)})
                except Exception as exc:
                    append('dropped.jsonl', {'job_id': job.id, 'queue': args.queue, 'reason': 'transfer_failed', 'error': str(exc)})
            else:
                if job.tasks:
                    append('dropped.jsonl', {'job_id': job.id, 'queue': args.queue, 'reason': 'no_outgoing_edge'})
                else:
                    append('findings.jsonl', {'job_id': job.id, 'source': job.payload.get('source'),
                          'check': job.payload.get('check'), 'status': job.status, 'error': job.error,
                          'findings': job.findings, 'verification': job.verification})
            pending.task_done()

    for i in range(args.slots):
        threading.Thread(target=process, args=(i,), daemon=True).start()
    if args.transport == 'grpc':
        grpc_serve(50051, receive, 12)
    else:
        for message in fifo_read(f'in-{args.queue}'):
            receive(message)

def producer(args):
    configs = list(csv.DictReader((DATA / 'qconf.csv').open(newline='')))
    payloads = {r['job_id']: r for r in map(json.loads, (DATA / 'jobs.jsonl').read_text().splitlines())}
    todo = []
    for cfg in configs:
        if not cfg['Source']:
            continue
        with (DATA / cfg['Source']).open(newline='') as f:
            for row in csv.DictReader(f):
                job = Job(id=row['Job Id'], arrival=int(row['arrival time']),
                          tasks=json.loads(row['Tasks']), payload=payloads[row['Job Id']])
                todo.append((job.arrival, cfg['Sys ID'], encode(job)))
    todo.sort(key=lambda x: (x[0], x[2]['id']))
    OUT.mkdir(parents=True, exist_ok=True)
    # These output paths are new for each generated run.
    start = time.perf_counter()
    for arrival, dest, job in todo:
        delay = arrival * args.step_seconds - (time.perf_counter() - start)
        if delay > 0:
            time.sleep(delay)
        if args.transport == 'grpc':
            grpc_send(f'queue-{dest}:50051', {'job': job})
        else:
            fifo_send(f'in-{dest}', {'job': job})
    print(f'Injected {len(todo)} jobs. Waiting for completed/dropped totals...', flush=True)
    deadline = time.monotonic() + args.timeout
    def count(name):
        p = OUT / name
        return sum(1 for _ in p.open()) if p.exists() else 0
    while True:
        completed = count('findings.jsonl')
        dropped = count('dropped.jsonl')
        if completed + dropped >= len(todo):
            break
        if time.monotonic() > deadline:
            raise TimeoutError(f'Timed out: {completed} completed, {dropped} dropped of {len(todo)}')
        time.sleep(0.3)

    records = [json.loads(s) for s in (OUT / 'findings.jsonl').read_text().splitlines()]
    seen = {(r.get('source'), f['rule'], f['line']) for r in records for f in r['findings']}
    summary = {'source_jobs': len(todo), 'completed': completed, 'dropped': dropped,
               'analysis_errors': sum(bool(r['error']) for r in records),
               'queues': len(configs), 'transport': args.transport,
               'elapsed_seconds': time.perf_counter() - start,
               'unique_candidates': len(seen), 'physical_hardware_connected': False,
               'unexpected_unlocks': 0}
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    
    with (OUT / 'out.wl').open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Job Id', 'arrival time', 'Tasks'])
        for r in records:
            writer.writerow([r['job_id'], '', '[]'])
    print(json.dumps(summary, indent=2), flush=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('role', choices=['queue', 'function', 'producer'])
    ap.add_argument('--transport', choices=['fifo', 'grpc'], required=True)
    ap.add_argument('--queue')
    ap.add_argument('--stage')
    ap.add_argument('--edges', default='[]')
    ap.add_argument('--slots', type=int, default=1)
    ap.add_argument('--capacity', type=int, default=100000)
    ap.add_argument('--step-seconds', type=float, default=0.001)
    ap.add_argument('--timeout', type=float, default=600)
    args = ap.parse_args()
    if args.role == 'function':
        function_service(args)
    elif args.role == 'queue':
        queue_service(args)
    else:
        producer(args)

if __name__ == '__main__':
    main()
