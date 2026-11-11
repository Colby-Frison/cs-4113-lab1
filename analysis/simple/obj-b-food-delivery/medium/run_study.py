#!/usr/bin/env python3
"""Collect/replot the 20-queue food-delivery experiment, preserving every outcome."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import subprocess
import sys
import tarfile

HERE=Path(__file__).resolve().parent
RESULTS=HERE/'results'
RUNS=HERE/'runs'
REPO=next(p for p in HERE.parents if (p/'implementation/obj-b.food-delivery').is_dir())
APP=REPO/'implementation/obj-b.food-delivery'
sys.path.insert(0,str(APP))
from run_network import run
SPEC=json.loads((HERE/'study.json').read_text())


def configuration(s,seed):return {**SPEC['baseline'],'couriers':s['couriers'],'seed':seed}


def collect():
    RUNS.mkdir(exist_ok=True)
    files=[HERE/'study.json',APP/'run_network.py',APP/'fd/network_config.py',APP/'fd/network_analysis.py',APP/'fd/workload.py',APP/'fd/services.py',APP/'fd/dispatch.py',APP/'cpp/server.cpp',APP/'cpp/build/food_server']
    hashes={str(p.relative_to(REPO)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    provenance=RUNS/'provenance.json'
    if provenance.exists():
        if json.loads(provenance.read_text())['sha256']!=hashes:raise RuntimeError('Collection inputs changed; do not mix experiments.')
    else:
        import grpc
        provenance.write_text(json.dumps({'sha256':hashes,'python':sys.version,'grpcio':grpc.__version__,
            'platform':platform.platform(),'git_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
            'note':'Sequential local wall-clock executions. Fixed RNG seeds do not fix OS scheduling.'},indent=2)+'\n')
    schedule=[]
    for seed in SPEC['seeds']:
        scenarios=list(SPEC['scenarios']);random.Random(5113+seed).shuffle(scenarios)
        if seed==1:scenarios.sort(key=lambda s:s['name']!='baseline')
        schedule.extend((s,seed) for s in scenarios)
    (RUNS/'schedule.json').write_text(json.dumps([{'scenario':s['name'],'seed':seed} for s,seed in schedule],indent=2)+'\n')
    for s,seed in schedule:
        root=RUNS/s['name']/f'seed-{seed}';config=configuration(s,seed)
        result=root/'analysis/summary.json'
        archive=root.with_suffix('.tar.gz')
        if result.exists() or archive.exists():
            if result.exists():
                actual=json.loads((root/'config.json').read_text())
                summary=json.loads(result.read_text())
            else:
                with tarfile.open(archive) as tar:
                    actual=json.load(tar.extractfile(f'{root.name}/config.json'))
                    summary=json.load(tar.extractfile(f'{root.name}/analysis/summary.json'))
            if any(actual[k]!=v for k,v in config.items()):raise RuntimeError('existing config mismatch')
            if summary['incomplete'] or summary['generated']!=config['count']:raise RuntimeError('invalid saved run')
        else:
            print(f"RUN {s['name']} seed={seed} regional couriers={s['couriers']}",flush=True)
            summary=run(root,config,timeout=240)
        print(f"DONE {s['name']} seed={seed}: delivered={summary['delivered']} dropped={summary['dropped']} latency={summary['mean_latency_seconds']:.4f}s throughput={summary['throughput_orders_per_second']:.2f}/s",flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--collect',action='store_true');p.add_argument('--report',action='store_true')
    args=p.parse_args()
    if not args.collect and not args.report:p.error('choose --collect and/or --report')
    os.environ.setdefault('MPLBACKEND','Agg')
    if args.collect:collect()
    if args.report:
        from report_study import report
        report()
