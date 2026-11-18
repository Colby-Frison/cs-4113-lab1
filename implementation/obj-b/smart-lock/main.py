#!/usr/bin/env python3
"""CLI entry point"""
import argparse
import json
from pathlib import Path
from simulator import Simulator

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--engine',choices=['python','cpp-grpc'],default='python')
    p.add_argument('--endpoint',default='127.0.0.1:50051')
    p.add_argument('--arrival-step-seconds',type=float,default=.001)
    p.add_argument('--tick-us',type=int,default=100)
    p.add_argument('--sample-ticks',type=int,default=10)
    p.add_argument('--max-metric-rows',type=int,default=200000)
    p.add_argument('--max-seconds',type=float,default=300)
    p.add_argument('--rpc-timeout',type=float,default=30)
    p.add_argument('--test-catalog',type=Path)
    p.add_argument('--serial-port')
    p.add_argument('--baud',type=int,default=115200)
    a=p.parse_args()
    try:
        import math #eh might be needed why not

        if min(a.workers,a.tick_us,a.sample_ticks,a.max_metric_rows,a.max_seconds,a.rpc_timeout)<1 or not math.isfinite(a.arrival_step_seconds) or a.arrival_step_seconds<0:
            raise ValueError('positive settings and nonnegative arrival step required')
        print(json.dumps(Simulator(a).run(),indent=2))

    except (ValueError,OSError,TimeoutError,ImportError) as exc:p.error(str(exc))

if __name__=='__main__':main()
