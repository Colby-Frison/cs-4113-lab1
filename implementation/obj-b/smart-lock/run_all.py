#!/usr/bin/env python3
"""One command: generate workload, run application, invoke existing Obj A tools."""
import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
import sys

APP=Path(__file__).resolve().parent
sys.path.insert(0,str(APP/'scripts'))
from analyze_all import analyze
from cpp_service import cpp_service

def main():

    # cmds for running broken up a bit
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-root',type=Path,default=Path('firmware-source'))
    p.add_argument('--out',type=Path,default=Path('runs/ready-baseline'))
    p.add_argument('--tools-root',type=Path,default=APP.parent.parent/'obj-a')
    p.add_argument('--queues',type=int,default=6)
    p.add_argument('--mode',choices=['monolithic','decomposed'],default='decomposed')
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--servers',type=int,default=1)
    p.add_argument('--passes',type=int,default=1)
    p.add_argument('--seed',type=int,default=5113)
    p.add_argument('--arrival-step-seconds',type=float,default=.001)
    p.add_argument('--engine',choices=['python','cpp-grpc'],default='python')
    p.add_argument('--endpoint',help='Existing C++ service; otherwise start the built local service automatically')
    p.add_argument('--no-plots',action='store_true')
    p.add_argument('--test-catalog',type=Path)
    p.add_argument('--serial-port')
    p.add_argument('--max-metric-rows',type=int,default=200000)
    p.add_argument('--max-seconds',type=float,default=600)
    a=p.parse_args()

    try:
        out=a.out.resolve()
        if out.exists():raise ValueError('run directory exists, choose another --out') #preventing overwriting, could timestamp, just lazy

        # Finite corpus uses three checks, give ample arrival slots without an arbitrary job cutoff.

        # just filtering the files to those that are C/C++/Arduino and not in other directories I don't waanna mess with
        files=sum(1 for f in a.source_root.rglob('*') if f.is_file() and f.suffix.lower() in {'.c','.cc','.cpp','.cxx','.h','.hpp','.ino'})
        horizon=max(9999,files*a.passes*3*2)

        # glorified command
        subprocess.run([sys.executable,str(APP/'workload-generator/workload_generator.py'),
            '--source-root',str(a.source_root.resolve()),'--out-dir',str(out/'inputs'),'--interval',f'0:{horizon}',
            '--queues',str(a.queues),'--mode',a.mode,'--servers',str(a.servers),'--passes',str(a.passes),'--seed',str(a.seed)],check=True)
        
        context=cpp_service(APP,a.endpoint) if a.engine=='cpp-grpc' else nullcontext('')
        
        # run the main.py with the generated workload and specified parameters
        with context as endpoint:
            cmd=[sys.executable,str(APP/'main.py'),'--config',str(out/'inputs/qconf.csv'),'--out',str(out/'outputs'),
                 '--workers',str(a.workers),'--engine',a.engine,'--arrival-step-seconds',str(a.arrival_step_seconds),
                 '--max-metric-rows',str(a.max_metric_rows),'--max-seconds',str(a.max_seconds)]
            
            if endpoint:cmd+=['--endpoint',endpoint]
            if a.test_catalog:cmd+=['--test-catalog',str(a.test_catalog.resolve())]
            if a.serial_port:cmd+=['--serial-port',a.serial_port]
            
            # the actual run, yay
            subprocess.run(cmd,check=True)
       
        if not a.no_plots:analyze(out/'outputs',a.tools_root)
        print('Run saved:',out)

    except (ValueError,OSError,subprocess.CalledProcessError) as exc:p.error(str(exc))

if __name__=='__main__':main()
