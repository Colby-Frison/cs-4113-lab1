"""Queue coordinator adapted to real application.

Service times are measured, not Sub-X ticks. Queue nodes have independent FCFS lines and logical server slots.
A bounded shared process/RPC worker pool supplies actual conc.
"""

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, wait, FIRST_COMPLETED
import csv
import json
from multiprocessing import get_context
from pathlib import Path
import time

from config_io import load_config, save_config
from event_log import EventLog
from job import task_parts
from metrics_writer import MetricsWriter
from queue_system import QueueSystem
from server_function import execute
from workload_io import load_workloads, write_sink

class Simulator:
    def __init__(self,args):

        self.args=args;self.configs=load_config(args.config)
        self.root,self.sources=load_workloads(args.config,self.configs)
        self.out=args.out.resolve()

        if self.out.exists():raise ValueError('output directory already exists; choose a new run')

        self.out.mkdir(parents=True)
        save_config(self.out/'qconf.csv',self.configs)
        self.log=EventLog(self.out/'events.log',args.tick_us)
        self.queues={c['Sys ID']:QueueSystem(c) for c in self.configs}
        self.order=list(self.queues);self.cursor=0;self.completed=[];self.dropped=[];self.sink=[]
        self.seen=set();self.board=None

        # just making sure if someone is running and they don't have hardware connected it'll still all run LMAO
        self.catalog=json.loads(args.test_catalog.read_text()) if args.test_catalog else {}

        if args.serial_port:
            from hardware import SerialVerifier
            if not self.catalog.get('firmware_build_id'):raise ValueError('hardware requires a target test catalog and build identity')
            self.board=SerialVerifier(args.serial_port,args.baud,args.rpc_timeout,self.catalog['firmware_build_id'])

    def verify(self,job):
        # loops through findings for the job, and for each finding, creates a key based on the job's sha256, rule, and line. 
        # If the key has been seen before, it marks the finding as a duplicate. Otherwise, it adds the key to the seen set and checks for test plans in the catalog that match the key. 
        # Depending on the number of matching plans and whether hardware is available, it updates the finding's status accordingly. If hardware is available, it attempts to verify the plan using the board and updates the finding with the result or any errors encountered.

        # That testing on hardware was the main point of the application, if there's enough space on the writeup I'll include a pic of it :)

        for finding in job.findings:
            key=(job.payload['sha256'],finding['rule'],finding['line'])
            record=dict(finding)

            if key in self.seen:record['status']='duplicate_not_retested'

            else:
                self.seen.add(key)
                plans=[p for p in self.catalog.get('tests',[]) if (p['source_sha256'],p['rule'],p['line'])==key]
                if len(plans)>1:record['status']='ambiguous_test_plan'
                elif not plans:record['status']='needs_target_specific_test'
                elif self.board is None:record['status']='awaiting_hardware'
                else:
                    try:record.update(self.board.verify(plans[0]))
                    except Exception as exc:record.update(status='hardware_error',error=str(exc))
            
            job.verification.append(record)
        return job

    def admit(self,qid,job,source):
        q=self.queues[qid]
        self.log.log(job.id,qid,'ARRIVE-VIA '+source)
        
        if q.admit(job):self.log.log(job.id,qid,'ENQUEUE',waiting=1)
        else:
            self.log.log(job.id,qid,'ERROR QUEUE-FULL',errors=1)
            self.dropped.append(dict(job_id=job.id,queue=qid,status='queue_full'))

    def run(self):
        a=self.args;start=time.perf_counter();pending={};source_index=0
        pool=(ProcessPoolExecutor(max_workers=a.workers,mp_context=get_context('spawn')) if a.engine=='python'
              else ThreadPoolExecutor(max_workers=a.workers))
        hardware_pool=ThreadPoolExecutor(max_workers=1)
        
        try:
            while source_index<len(self.sources) or pending or any(q.waiting for q in self.queues.values()):
                if time.perf_counter()-start>a.max_seconds:raise TimeoutError('run exceeded --max-seconds')
                
                # loops through pending futures, checks if done, and if so gets the results or exceptions, logs stuff
                for future in list(pending):
                    if not future.done():continue
                    qid,sid,original,tid,token=pending.pop(future);q=self.queues[qid]
                    try:job=future.result()
                    except Exception as exc:
                        job=original;job.status='analysis_error';job.error=str(exc)
                        self.log.log(job.id,qid,'ERROR SERVER-FAILED',errors=1)
                    self.log.log(job.id,qid,f'TASK-END {tid} {json.dumps(token)}')
                    self.log.log(job.id,qid,f'EXITS-SERVER {sid}',busy=-1);del q.busy[sid]

                    if job.tasks:job.tasks.pop(0)
                    dest=q.destination()

                    if dest:
                        self.log.log(job.id,qid,'DEPART-VIA '+dest);self.admit(dest,job,qid)

                    elif job.tasks:
                        self.log.log(job.id,qid,'ERROR NO-OUT-EDGE',errors=1)
                        self.dropped.append(dict(job_id=job.id,queue=qid,status='unfinished_tasks_no_edge'))
                    else:
                        tick=self.log.log(job.id,qid,'DEPART-VIA out.wl')
                        self.sink.append((job.id,tick));self.completed.append(job)

                # just admitting jobs that have arrived based on the elapsed time and the arrival step seconds
                elapsed=time.perf_counter()-start
                while source_index<len(self.sources) and self.sources[source_index][0]*a.arrival_step_seconds<=elapsed:
                    _,qid,job=self.sources[source_index];self.admit(qid,job,'in'+qid[1:]+'.wl');source_index+=1

                # first come first serve
                attempts=0
                while attempts<len(self.order):

                    qid=self.order[self.cursor%len(self.order)];self.cursor+=1;attempts+=1
                    q=self.queues[qid];sid=q.idle_server();stage=q.config['Server Function']

                    is_hardware=stage in ('Verify','VerifyReport')
                    active=sum(1 for x in pending.values() if self.queues[x[0]].config['Server Function'] not in ('Verify','VerifyReport'))
                    hw_active=any(self.queues[x[0]].config['Server Function'] in ('Verify','VerifyReport') for x in pending.values())

                    # checks if queue has waiting jobs and if idle
                    if not q.waiting or sid is None or (is_hardware and hw_active) or (not is_hardware and active>=a.workers):continue

                    # if queue has waiting jobs and is idle, pop the job from the queue and assign it to the server, log stuff, and submit the job to the appropriate executor
                    job=q.waiting.popleft();q.busy[sid]=job
                    tid,token=task_parts(job.tasks[0]) if job.tasks else (0,'W 1')
                    self.log.log(job.id,qid,f'ENTERS-SERVER {sid}',waiting=-1,busy=1)
                    self.log.log(job.id,qid,f'TASK-START {tid} {json.dumps(token)}')

                    # checks if the stage is a hardware verification stage, if so submits to the hardware pool, otherwise submits to the main pool. If the job has an error, creates a future with the job as the result.
                    if is_hardware:f=hardware_pool.submit(self.verify,job)
                    elif job.error:

                        from concurrent.futures import Future #quick fix, was bugging without this idk why man
                        f=Future();f.set_result(job)

                    else:f=pool.submit(execute,stage,job,str(self.root),a.engine,a.endpoint,a.rpc_timeout)
                    
                    pending[f]=(qid,sid,job,tid,token)
                    attempts=0

                # checks if pending futures and waits for first one to complete
                if pending:wait(list(pending),timeout=.002,return_when=FIRST_COMPLETED)

                elif source_index<len(self.sources):time.sleep(min(.002,max(0,self.sources[source_index][0]*a.arrival_step_seconds-(time.perf_counter()-start))))
        
        # catches exceptions so I can fix my crap
        finally:
            pool.shutdown(wait=True);hardware_pool.shutdown(wait=True)
            if self.board:self.board.close()
            self.log.flush()

        info=MetricsWriter(self.out/'qmet.csv').write(self.log,self.configs,a.max_metric_rows,a.sample_ticks)
        write_sink(self.out/'out.wl',self.sink,self.log.clock())

        # writes findings
        with (self.out/'findings.jsonl').open('w') as f:
            for job in self.completed:
                f.write(json.dumps(dict(job_id=job.id,source=job.payload['source'],sha256=job.payload['sha256'],
                                        check=job.payload['check'],status=job.status,error=job.error,
                                        findings=job.findings,verification=job.verification))+'\n')

        # just my fun messing round num unexpected unlcskldf (hate this keyboard)
        summary=dict(source_jobs=len(self.sources),completed=len(self.completed),dropped=len(self.dropped),
                     analysis_errors=sum(bool(j.error) for j in self.completed),queues=len(self.queues),workers=a.workers,
                     engine=a.engine,elapsed_seconds=time.perf_counter()-start,unique_candidates=len(self.seen),
                     physical_hardware_connected=self.board is not None,
                     unexpected_unlocks=sum(v['status']=='unexpected_unlock_observed' for j in self.completed for v in j.verification))
        
        (self.out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
        (self.out/'dropped.jsonl').write_text(''.join(json.dumps(j)+'\n' for j in self.dropped))
        return summary
