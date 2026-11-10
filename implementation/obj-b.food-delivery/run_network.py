#!/usr/bin/env python3
"""Run restaurant kitchens feeding shared regional courier queues over C++/gRPC."""
import argparse
from collections import deque
from concurrent import futures
from contextlib import ExitStack
from datetime import datetime
import json
import math
from pathlib import Path
import subprocess
import threading
import time

from fd import APP
from fd.dispatch import Dispatcher, file_logger
from fd.order import validate_order
from fd.workload import generate
from fd.network_analysis import analyze_network
from lib.events import arrive_via, error_queue_full
from lib.job import HEADER, parse_job_line


def validate_config(config):
    required={'restaurant_regions','restaurant_weights','kitchen_workers','couriers','kitchen_capacity','pickup_capacity','count','arrival_mean','time_scale','seed'}
    if set(config)!=required: raise ValueError('invalid network configuration keys')
    mapping=config['restaurant_regions']; couriers=config['couriers']; kitchens=config['kitchen_workers'];weights=config['restaurant_weights']
    if not mapping or not couriers or len(mapping)!=len(kitchens) or len(mapping)!=len(weights):
        raise ValueError('inconsistent restaurant configuration')
    if any(type(r) is not int or not 0<=r<len(couriers) for r in mapping) or set(mapping)!=set(range(len(couriers))):
        raise ValueError('each region must have restaurants and a valid region ID')
    if any(type(v) is not int or v<1 for v in kitchens+couriers+[config['kitchen_capacity'],config['pickup_capacity'],config['count']]):
        raise ValueError('worker counts/capacities/count must be positive integers')
    if type(config['seed']) is not int: raise ValueError('seed must be integer')
    if any(not math.isfinite(v) or v<=0 for v in weights): raise ValueError('invalid demand weights')
    if any(not math.isfinite(config[k]) or config[k]<0 for k in ('arrival_mean','time_scale')):
        raise ValueError('invalid timing configuration')


def run(root, config, *, binary=None, timeout=240):
    import grpc
    from lib.proto_gen import job_pb2, queue_to_server_pb2_grpc, server_to_queue_pb2_grpc
    validate_config(config)
    if not math.isfinite(timeout) or timeout<=0: raise ValueError('timeout must be positive')
    binary=Path(binary or APP/'cpp/build/food_server').resolve()
    if not binary.is_file(): raise ValueError('build cpp/build/food_server first')
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    if any(root.iterdir()): raise ValueError(f'refusing to overwrite {root}')
    (root/'config.json').write_text(json.dumps({**config,'transport':'grpc','server_language':'cpp'},indent=2)+'\n')
    n=len(config['restaurant_regions']); regions=len(config['couriers'])
    deadline=time.monotonic()+timeout
    queues=[deque() for _ in range(n)]; conditions=[threading.Condition() for _ in range(n)]
    qlogs=[file_logger(root/f'q{i}.log','Q') for i in range(n)]
    processes=[];channels=[];dispatchers=[];dispatch_threads=[];senders=[];failures=[]
    input_done=threading.Event(); source_done=threading.Event(); ingress=None

    def remaining():
        if failures: raise RuntimeError(f'network failed: {failures[0]}')
        for dispatcher in dispatchers:
            if dispatcher.failure: raise RuntimeError(f'dispatch failed: {dispatcher.failure}')
        for name,p in processes:
            if p.poll() is not None: raise RuntimeError(f'{name} exited; inspect its stderr')
        left=deadline-time.monotonic()
        if left<=0: raise TimeoutError('network run deadline exceeded')
        return left

    def sleep(seconds):
        end=time.monotonic()+seconds
        while True:
            remaining(); delay=end-time.monotonic()
            if delay<=0:return
            time.sleep(min(delay,.05))

    def visit(stub,job,source):
        req=job_pb2.SubmitJobRequest(source_edge=source,job=job_pb2.Job(job_id=job.job_id,arrival_time=job.arrival_time,
            raw_line=job.raw_line,tasks=[job_pb2.Task(tid=t.tid,wait_seconds=t.wait_seconds) for t in job.tasks]))
        if not stub.Submit(req,timeout=remaining()).accepted: raise RuntimeError('server refused visit')

    try:
        with ExitStack() as files:
            def launch(stage,qid,index,restaurants,directory,peer=None):
                directory.mkdir(exist_ok=True)
                name=f'{stage}-{index}';ready=directory/f'{name}.ready'
                args=[str(binary),'--stage',stage,'--qid',qid,'--server-id',str(index),
                      '--restaurants',','.join(map(str,restaurants)),'--logfile',str(directory/f'{name}.log'),
                      '--ready-file',str(ready),'--listen','127.0.0.1:0']
                args+=['--outgrpc',peer] if peer else ['--out',str(directory/f'sink-{index}.csv')]
                err=files.enter_context((directory/f'{name}.stderr').open('w'))
                p=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=err)
                processes.append((f'{qid}/{name}',p))
                while not ready.exists() or not ready.read_text().strip():
                    remaining();time.sleep(.01)
                channel=grpc.insecure_channel(f'127.0.0.1:{int(ready.read_text())}',options=[('grpc.enable_retries',0)])
                channels.append(channel);grpc.channel_ready_future(channel).result(timeout=remaining())
                return queue_to_server_pb2_grpc.QueueToServerStub(channel)

            for region in range(regions):
                restaurants=[r for r,v in enumerate(config['restaurant_regions']) if v==region]
                directory=root/f'region-{region}'
                couriers=[launch('courier',f'q{n+region}',i,restaurants,directory) for i in range(config['couriers'][region])]
                dispatcher=Dispatcher(len(couriers),config['pickup_capacity'],directory,qid=f'q{n+region}',
                    allowed_restaurants=restaurants,
                    delivery=lambda job,index,stubs=couriers,qid=f'q{n+region}':visit(stubs[index],job,qid))
                dispatchers.append(dispatcher)

            class Pickup(server_to_queue_pb2_grpc.ServerToQueueServicer):
                def Submit(self,request,context):
                    try:
                        job=parse_job_line(request.job.raw_line); restaurant=validate_order(job).restaurant
                        if restaurant>=n or request.source_edge!=f'q{restaurant}':
                            raise ValueError('restaurant/source route mismatch')
                        dispatcher=dispatchers[config['restaurant_regions'][restaurant]]
                        with dispatcher.condition:
                            if dispatcher.stopping:
                                context.abort(grpc.StatusCode.UNAVAILABLE,'dispatcher stopped')
                        return job_pb2.SubmitJobResponse(accepted=dispatcher.admit(job,request.source_edge))
                    except ValueError as exc:
                        context.abort(grpc.StatusCode.INVALID_ARGUMENT,str(exc))
                    except Exception as exc:
                        failures.append(exc)
                        context.abort(grpc.StatusCode.INTERNAL,str(exc))
            ingress=grpc.server(futures.ThreadPoolExecutor(max_workers=sum(config['kitchen_workers'])))
            server_to_queue_pb2_grpc.add_ServerToQueueServicer_to_server(Pickup(),ingress)
            port=ingress.add_insecure_port('127.0.0.1:0')
            if not port:raise RuntimeError('pickup ingress bind failed')
            ingress.start()
            def dispatch_run(dispatcher):
                try:dispatcher.run(input_done=input_done)
                except Exception as exc:failures.append(exc)
            for dispatcher in dispatchers:
                thread=threading.Thread(target=dispatch_run,args=(dispatcher,));thread.start();dispatch_threads.append(thread)
            workers=[]
            for restaurant in range(n):
                # Unique worker index keeps top-level log/ready names distinct.
                workers.append([launch('kitchen',f'q{restaurant}',sum(config['kitchen_workers'][:restaurant])+i,
                    [restaurant],root,f'127.0.0.1:{port}') for i in range(config['kitchen_workers'][restaurant])])

            def sender(restaurant,stub):
                try:
                    while True:
                        with conditions[restaurant]:
                            conditions[restaurant].wait_for(lambda: queues[restaurant] or source_done.is_set())
                            if not queues[restaurant]:return
                            job=queues[restaurant].popleft()
                        visit(stub,job,f'q{restaurant}')
                except Exception as exc:failures.append(exc)
            for restaurant,stubs in enumerate(workers):
                for stub in stubs:
                    thread=threading.Thread(target=sender,args=(restaurant,stub));thread.start();senders.append(thread)
            record=files.enter_context((root/'jobs.csv').open('w'))
            class Output:
                def write(self,line):
                    remaining();record.write(line)
                    if line.strip()==HEADER:return
                    job=parse_job_line(line);r=validate_order(job).restaurant
                    with conditions[r]:
                        if len(queues[r])>=config['kitchen_capacity']:
                            qlogs[r].info('%s',error_queue_full(job.job_id,f'q{r}'))
                        else:
                            qlogs[r].info('%s',arrive_via(job.job_id,f'q{r}','orders'))
                            queues[r].append(job);conditions[r].notify()
                def flush(self):record.flush()
            generate(Output(),count=config['count'],seed=config['seed'],arrival_mean=config['arrival_mean'],
                     time_scale=config['time_scale'],restaurant_weights=config['restaurant_weights'],sleep=sleep)
            source_done.set()
            for condition in conditions:
                with condition:condition.notify_all()
            for thread in senders:
                while thread.is_alive():thread.join(min(.05,remaining()))
            remaining();input_done.set()
            for thread in dispatch_threads:
                while thread.is_alive():thread.join(min(.05,remaining()))
            remaining()
    finally:
        source_done.set();input_done.set()
        for queue,condition in zip(queues,conditions):
            with condition:queue.clear();condition.notify_all()
        for dispatcher in dispatchers:
            with dispatcher.condition:dispatcher.stopping=True;dispatcher.condition.notify_all()
        if ingress:ingress.stop(0).wait()
        for channel in channels:channel.close()
        for _,p in processes:
            if p.poll() is None:p.terminate()
        for _,p in processes:
            try:p.wait(timeout=2)
            except subprocess.TimeoutExpired:p.kill();p.wait()
        for thread in senders+dispatch_threads:thread.join(timeout=2)
        for logger in qlogs:
            for handler in logger.handlers:handler.close()
        for dispatcher in dispatchers:
            for handler in dispatcher.log.handlers:handler.close()
    return analyze_network(root,config)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--workdir',type=Path)
    parser.add_argument('--timeout',type=float,default=240)
    args=parser.parse_args()
    root=args.workdir or APP/'runs'/(datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'-network')
    print(json.dumps(run(root,json.loads(args.config.read_text()),timeout=args.timeout),indent=2))
    print(f'Artifacts: {root.resolve()}')


if __name__=='__main__':main()
