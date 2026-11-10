#!/usr/bin/env python3
"""Host a regional network in one asyncio coordinator and two C++ gRPC processes."""
import argparse
import asyncio
import csv
from datetime import datetime
import hashlib
import io
import json
import math
import shutil
from pathlib import Path
import time

from fd import APP
from fd.dispatch import file_logger
from fd.network_analysis import analyze_network
from fd.order import validate_order, WAIT_PRECISION
from fd.workload import generate
from lib.events import arrive_via, error_queue_full
from lib.job import HEADER, format_job_line, parse_job_line, iter_jobs
from run_network import validate_config


def workload(config):
    """Reuse the app RNGs but record an absolute, sub-millisecond arrival schedule."""
    offsets=[]; elapsed=0.0
    def advance(delay):
        nonlocal elapsed
        elapsed+=delay; offsets.append(elapsed)
    output=io.StringIO()
    generate(output,count=config['count'],seed=config['seed'],arrival_mean=config['arrival_mean'],
             time_scale=config['time_scale'],restaurant_weights=config['restaurant_weights'],
             sleep=advance,clock=lambda:0)
    return list(zip(offsets,iter_jobs(output.getvalue().splitlines())))


class HostedQueue:
    """All mutation runs on one asyncio loop; a courier is reserved through RPC output."""
    def __init__(self,qid,workers,capacity,log):
        self.qid=qid;self.queue=asyncio.Queue(maxsize=capacity);self.log=log
        self.waiting={};self.accepted=set();self.dropped=0
        self.workers={i:{'status':'available','order_id':None,'completed':0} for i in range(workers)}

    def admit(self,job,source):
        if job.job_id in self.accepted:raise ValueError('duplicate queue admission')
        if self.queue.full():
            self.log.info('%s',error_queue_full(job.job_id,self.qid));self.dropped+=1;return False
        self.accepted.add(job.job_id);self.waiting[job.job_id]=None
        self.log.info('%s',arrive_via(job.job_id,self.qid,source))
        self.queue.put_nowait(job)
        return True

    def snapshot(self,path):
        temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'couriers':self.workers,'waiting':list(self.waiting)},indent=2)+'\n')
        temporary.replace(path)


async def execute(root,config,binary,timeout):
    import grpc
    from lib.proto_gen import job_pb2,queue_to_server_pb2_grpc
    restaurants=len(config['restaurant_regions']);regions=len(config['couriers'])
    processes=[];channels=[];files=[];logs=[];queues=[]
    plan=workload(config)
    with (root/'planned-arrivals.csv').open('w',newline='') as f:
        writer=csv.writer(f);writer.writerow(['job_id','planned_elapsed_seconds'])
        writer.writerows((job.job_id,f'{offset:.9f}') for offset,job in plan)
    host_configs={'kitchen':[],'courier':[]}
    for qid,workers in enumerate(config['kitchen_workers']+config['couriers']):
        kitchen=qid<restaurants;region=config['restaurant_regions'][qid] if kitchen else qid-restaurants
        directory=root if kitchen else root/f'region-{region}';directory.mkdir(exist_ok=True)
        log=file_logger(directory/f'q{qid}.log','Q');logs.append(log)
        queues.append(HostedQueue(f'q{qid}',workers,config['kitchen_capacity'] if kitchen else config['pickup_capacity'],log))
        stage='kitchen' if kitchen else 'courier'
        host_configs[stage].append({'qid':f'q{qid}','stage':stage,'workers':workers,
            'restaurants':[qid] if kitchen else [r for r,v in enumerate(config['restaurant_regions']) if v==region],
            'logfile':str(directory/f'{stage}-host-q{qid}.log'),
            'destination':f'q{restaurants+region}' if kitchen else str(directory/'sink-0.csv')})
    stop=asyncio.Event();lag_values=[];start=None
    deadline=time.monotonic()+timeout
    def remaining():
        left=deadline-time.monotonic()
        if left<=0:raise TimeoutError('hosted network deadline exceeded')
        return left
    try:
        async with asyncio.timeout(timeout):
            # Start both children before gRPC creates client-side threads.
            for stage,entries in host_configs.items():
                path=root/f'{stage}-host.json';path.write_text(json.dumps({'queues':entries},indent=2)+'\n')
                ready=root/f'{stage}-host.ready';err=(root/f'{stage}-host.stderr').open('w');files.append(err)
                process=await asyncio.create_subprocess_exec(str(binary),'--config',str(path),'--ready-file',str(ready),
                    '--listen','127.0.0.1:0',stdout=asyncio.subprocess.DEVNULL,stderr=err)
                processes.append((stage,process,ready))
            stubs={}
            for stage,process,ready in processes:
                while not ready.exists() or not ready.read_text().strip():
                    if process.returncode is not None:raise RuntimeError(f'{stage} host exited; inspect stderr')
                    await asyncio.sleep(.01)
                channel=grpc.aio.insecure_channel(f'127.0.0.1:{int(ready.read_text())}',options=[('grpc.enable_retries',0)])
                channels.append(channel);await asyncio.wait_for(channel.channel_ready(),remaining())
                stubs[stage]=queue_to_server_pb2_grpc.QueueToServerStub(channel)
            (root/'runtime.json').write_text(json.dumps({'backend':'hosted-async','server_processes':2,
                'logical_queues':len(queues),'logical_workers':sum(len(q.workers) for q in queues),
                'arrival_policy':'absolute planned offsets; late arrivals injected immediately; lag recorded',
                'hosts':{stage:p.pid for stage,p,_ in processes}},indent=2)+'\n')
            async def worker(qid,index):
                q=queues[qid];state=q.workers[index];kitchen=qid<restaurants
                while True:
                    job=await q.queue.get()
                    if job is None:q.queue.task_done();return
                    del q.waiting[job.job_id]
                    if state['status']!='available':raise RuntimeError('worker already reserved')
                    state.update(status='busy',order_id=job.job_id)
                    q.log.info('dispatch-assigned worker_%s %s',index,job.job_id)
                    request=job_pb2.SubmitJobRequest(source_edge=q.qid,job=job_pb2.Job(job_id=job.job_id,
                        arrival_time=job.arrival_time,raw_line=job.raw_line,
                        tasks=[job_pb2.Task(tid=t.tid,wait_seconds=t.wait_seconds) for t in job.tasks]))
                    try:
                        response=await stubs['kitchen' if kitchen else 'courier'].Submit(request,
                            metadata=(('worker-id',str(index)),),timeout=remaining())
                        if not response.accepted:raise RuntimeError('host refused service')
                        if kitchen:
                            region=config['restaurant_regions'][qid]
                            queues[restaurants+region].admit(job,q.qid)
                        state['completed']+=1;state.update(status='available',order_id=None)
                        q.log.info('dispatch-released worker_%s %s',index,job.job_id)
                        q.queue.task_done()
                    except BaseException:
                        state['status']='failed';raise
            async def monitor():
                last_snapshot=0
                while not stop.is_set():
                    for stage,p,_ in processes:
                        if p.returncode is not None:raise RuntimeError(f'{stage} host exited: {p.returncode}')
                    if time.monotonic()-last_snapshot>=1:
                        for region in range(regions):queues[restaurants+region].snapshot(root/f'region-{region}/dispatch-state.json')
                        last_snapshot=time.monotonic()
                    try:await asyncio.wait_for(stop.wait(),.1)
                    except TimeoutError:pass
            async with asyncio.TaskGroup() as group:
                group.create_task(monitor())
                for qid,q in enumerate(queues):
                    for index in q.workers:group.create_task(worker(qid,index))
                with (root/'jobs.csv').open('w') as jobs,(root/'arrival-lag.csv').open('w',newline='') as lag:
                    jobs.write(HEADER+'\n');writer=csv.writer(lag)
                    writer.writerow(['job_id','planned_elapsed_seconds','actual_elapsed_seconds','lag_seconds'])
                    start=time.monotonic()
                    for seq,(offset,planned) in enumerate(plan):
                        delay=start+offset-time.monotonic()
                        if delay>0:await asyncio.sleep(delay)
                        elif seq%32==0:await asyncio.sleep(0) # A catch-up burst must not starve completions.
                        actual=time.monotonic()-start;lag_values.append(max(0,actual-offset))
                        job=parse_job_line(format_job_line(planned.job_id,time.time(),planned.tasks,wait_precision=WAIT_PRECISION))
                        jobs.write(job.raw_line+'\n');writer.writerow([job.job_id,f'{offset:.9f}',f'{actual:.9f}',f'{max(0,actual-offset):.9f}'])
                        queues[validate_order(job).restaurant].admit(job,'orders')
                    jobs.flush();generation_seconds=time.monotonic()-start
                for group_queues in (queues[:restaurants],queues[restaurants:]):
                    await asyncio.gather(*(q.queue.join() for q in group_queues))
                    for q in group_queues:
                        for _ in q.workers:await q.queue.put(None)
                stop.set()
            for region in range(regions):queues[restaurants+region].snapshot(root/f'region-{region}/dispatch-state.json')
            lag_values.sort()
            return {'backend':'hosted-async','server_processes':2,'generation_seconds':generation_seconds,
                    'planned_generation_seconds':plan[-1][0], 'mean_injection_lag_seconds':sum(lag_values)/len(lag_values),
                    'p95_injection_lag_seconds':lag_values[int(.95*(len(lag_values)-1))],
                    'max_injection_lag_seconds':max(lag_values),
                    'planned_arrivals_sha256':hashlib.sha256((root/'planned-arrivals.csv').read_bytes()).hexdigest()}
    finally:
        stop.set()
        for region in range(regions):queues[restaurants+region].snapshot(root/f'region-{region}/dispatch-state.json')
        for channel in channels:await channel.close()
        for _,process,_ in processes:
            if process.returncode is None:process.terminate()
        for _,process,_ in processes:
            try:await asyncio.wait_for(process.wait(),3)
            except TimeoutError:process.kill();await process.wait()
        for f in files:f.close()
        for log in logs:
            for handler in log.handlers:handler.close()


def run(root,config,*,binary=None,timeout=600,analyze=True):
    validate_config(config)
    if not math.isfinite(timeout) or timeout<=0:raise ValueError('timeout must be positive')
    binary=Path(binary or APP/'cpp/build/food_host').resolve()
    if not binary.is_file():raise ValueError('build cpp/build/food_host first')
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    if any(root.iterdir()):raise ValueError(f'refusing to overwrite {root}')
    (root/'config.json').write_text(json.dumps({**config,'transport':'grpc','server_language':'cpp','backend':'hosted-async'},indent=2)+'\n')
    try:
        runtime=asyncio.run(execute(root,config,binary,timeout))
        if analyze:
            result=analyze_network(root,config)
            # Keep genuinely idle queues in smoke/pilot analyses too.
            n=len(config['restaurant_regions'])
            visited={int(line.split()[3][1:]) for line in (root/'analysis/events.log').read_text().splitlines()}
            missing=set(range(n+len(config['couriers'])))-visited
            if missing:
                qmet=root/'analysis/qmet.csv';temp=qmet.with_suffix('.tmp')
                with qmet.open() as source,temp.open('w') as target:
                    target.write(next(source))
                    for qid in sorted(missing):target.write(f'0,{qid},0,0,0\n')
                    shutil.copyfileobj(source,target)
                temp.replace(qmet)
                with (root/'analysis/qconf.csv').open('a') as f:
                    for qid in sorted(missing):
                        kitchen=qid<n
                        capacity=config['kitchen_capacity'] if kitchen else config['pickup_capacity']
                        workers=config['kitchen_workers'][qid] if kitchen else config['couriers'][qid-n]
                        f.write(f'q{qid},{capacity},{workers},wait,1,,,\n')
            result.update(runtime)
            (root/'analysis/summary.json').write_text(json.dumps(result,indent=2)+'\n')
        else:result=runtime
        (root/'status.json').write_text(json.dumps({'status':'complete','analyzed':analyze},indent=2)+'\n')
        return result
    except BaseException as exc:
        (root/'status.json').write_text(json.dumps({'status':'failed','error':repr(exc)},indent=2)+'\n')
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--workdir',type=Path)
    parser.add_argument('--timeout',type=float,default=600)
    args=parser.parse_args()
    root=args.workdir or APP/'runs'/(datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'-hosted')
    print(json.dumps(run(root,json.loads(args.config.read_text()),timeout=args.timeout),indent=2))
    print(f'Artifacts: {root.resolve()}')


if __name__=='__main__':main()
