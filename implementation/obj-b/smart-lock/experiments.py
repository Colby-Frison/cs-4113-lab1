#!/usr/bin/env python3
"""Four queue scales for the later analysis stuff, seven sets each, workloads/results/Obj A plots."""
import argparse
import csv
import json
import math
from pathlib import Path
import subprocess
import sys
from contextlib import nullcontext
from run_all import APP
sys.path.insert(0,str(APP/'scripts')) # quick fix
from cpp_service import cpp_service

SCALES={'small':6,'medium':16,'large':128,'very-large':1024}

PROFILES=[('baseline',.001,4,1,'decomposed'),('slower-arrivals',.002,4,1,'decomposed'),
          ('burst-arrivals',0,4,1,'decomposed'),('one-worker',.001,1,1,'decomposed'),
          ('eight-workers',.001,8,1,'decomposed'),('two-slots',.001,4,2,'decomposed'),
          ('monolithic',.001,4,1,'monolithic')]

def main():

    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-root',type=Path,default=Path('firmware-source'))
    p.add_argument('--out',type=Path,default=Path('runs/experiment-suite'))
    p.add_argument('--tools-root',type=Path,default=APP.parent.parent/'obj-a')
    p.add_argument('--scales',nargs='+',choices=list(SCALES),default=list(SCALES))
    p.add_argument('--engine',choices=['python','cpp-grpc'],default='python')
    p.add_argument('--no-plots',action='store_true')
    p.add_argument('--jobs-per-queue',type=int,default=5)
    p.add_argument('--max-metric-rows',type=int,default=30000)
    p.add_argument('--seed',type=int,default=5113)
    a=p.parse_args()

    try:
        if a.out.exists():raise ValueError('suite output exists; choose a new --out')

        # filter source files to those that are C/C++/Arduino and not in other directories I don't waanna mess with
        files=[f for f in a.source_root.rglob('*') if f.is_file() and f.suffix.lower() in {'.c','.cc','.cpp','.cxx','.h','.hpp','.ino'} and not any(x in {'.git','build','.pio','node_modules'} for x in f.parts)]

        if not files or a.jobs_per_queue<1:raise ValueError('need source files and jobs-per-queue>=1')

        a.out.mkdir(parents=True)
        records=[]

        with (cpp_service(APP) if a.engine=='cpp-grpc' else nullcontext('')) as endpoint:

            for scale in a.scales:

                # just quick one liner for num queues/passes
                n=SCALES[scale];passes=max(1,math.ceil(n*a.jobs_per_queue/(len(files)*3)))

                for profile,arrival,workers,servers,mode in PROFILES:
                    target=a.out/scale/profile

                    #Just run all on it, glorified call
                    cmd=[sys.executable,str(APP/'run_all.py'),'--source-root',str(a.source_root.resolve()),'--out',str(target.resolve()),
                         '--tools-root',str(a.tools_root.resolve()),'--queues',str(n),'--passes',str(passes),'--seed',str(a.seed),
                         '--workers',str(workers),'--servers',str(servers),'--mode',mode,'--engine',a.engine,
                         '--arrival-step-seconds',str(arrival),'--max-metric-rows',str(a.max_metric_rows)]

                    if endpoint:cmd+=['--endpoint',endpoint]
                    if a.no_plots:cmd+=['--no-plots']

                    subprocess.run(cmd,check=True) #yay

    except (ValueError,OSError,subprocess.CalledProcessError) as exc:p.error(str(exc))

if __name__=='__main__':main()
