#!/usr/bin/env python3
"""Collect/replot the 200-queue hosted study. Pilot runs require a separate output."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import sys
import tarfile

CODE=Path(__file__).resolve().parent
REPO=next(p for p in CODE.parents if (p/'implementation/obj-b.food-delivery').is_dir())
APP=REPO/'implementation/obj-b.food-delivery'
sys.path.insert(0,str(APP))
from run_hosted import run
DEFAULT=json.loads((CODE/'study.json').read_text())
HERE=CODE;RESULTS=HERE/'results';RUNS=HERE/'runs';SPEC=copy.deepcopy(DEFAULT)


def configure(output=None,count=None,seeds=None,smoke=False):
    global HERE,RESULTS,RUNS,SPEC
    HERE=Path(output).expanduser().resolve() if output else CODE
    RESULTS=HERE/'results';RUNS=HERE/'runs'
    if (smoke or count is not None or seeds is not None) and HERE==CODE:
        raise ValueError('Use --output for pilot/count/seed overrides; preserve the canonical study.')
    saved=RUNS/'study.json'
    SPEC=json.loads(saved.read_text()) if saved.exists() else copy.deepcopy(DEFAULT)
    requested=copy.deepcopy(SPEC)
    if smoke:
        requested=copy.deepcopy(DEFAULT);requested['baseline']['count']=5000;requested['seeds']=[1]
        requested['scenarios']=[requested['scenarios'][0]]
    if count is not None:
        if count<1:raise ValueError('count must be positive')
        requested['baseline']['count']=count
    if seeds is not None:
        if not seeds or len(set(seeds))!=len(seeds):raise ValueError('provide distinct seeds')
        requested['seeds']=seeds
    requested['representative_seed']=requested['seeds'][0]
    if saved.exists() and requested!=SPEC:raise ValueError('saved experiment differs; use a fresh --output directory')
    SPEC=requested


def configuration(s,seed):return {**SPEC['baseline'],'couriers':s['couriers'],'seed':seed}


def collect(timeout=600):
    import grpc
    RUNS.mkdir(parents=True,exist_ok=True)
    binary=APP/'cpp/build/food_host'
    files=[APP/'run_hosted.py',APP/'run_network.py',*sorted((APP/'fd').glob('*.py')),
           APP/'cpp/host.cpp',APP/'cpp/common.hpp',APP/'cpp/CMakeLists.txt',binary]
    hashes={str(p.relative_to(REPO)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    hashes['experiment']=hashlib.sha256(json.dumps(SPEC,sort_keys=True).encode()).hexdigest()
    provenance=RUNS/'provenance.json'
    if provenance.exists():
        if json.loads(provenance.read_text())['sha256']!=hashes:raise RuntimeError('Collection inputs changed; use a new output directory.')
    else:
        provenance.write_text(json.dumps({'sha256':hashes,'platform':platform.platform(),'python':sys.version,
            'grpcio':grpc.__version__,'logical_cpus':os.cpu_count(),'backend':'hosted-async'},indent=2)+'\n')
    (RUNS/'study.json').write_text(json.dumps(SPEC,indent=2)+'\n')
    schedule=[]
    for seed in SPEC['seeds']:
        scenarios=list(SPEC['scenarios']);random.Random(5113+seed).shuffle(scenarios)
        if seed==SPEC['representative_seed']:scenarios.sort(key=lambda s:s['name']!='baseline')
        schedule.extend((s,seed) for s in scenarios)
    (RUNS/'schedule.json').write_text(json.dumps([{'scenario':s['name'],'seed':seed} for s,seed in schedule],indent=2)+'\n')
    for s,seed in schedule:
        root=RUNS/s['name']/f'seed-{seed}';config=configuration(s,seed);archive=root.with_suffix('.tar.gz')
        if (root/'analysis/summary.json').exists() or archive.exists():
            if archive.exists() and not root.exists():
                with tarfile.open(archive) as tar:
                    actual=json.load(tar.extractfile(f'{root.name}/config.json'))
                    summary=json.load(tar.extractfile(f'{root.name}/analysis/summary.json'))
                    status=json.load(tar.extractfile(f'{root.name}/status.json'))
            else:
                actual=json.loads((root/'config.json').read_text());summary=json.loads((root/'analysis/summary.json').read_text())
                status=json.loads((root/'status.json').read_text())
            if any(actual[k]!=v for k,v in config.items()) or status['status']!='complete':raise RuntimeError('saved run mismatch')
            if summary['incomplete'] or summary['generated']!=config['count']:raise RuntimeError('invalid saved run')
            print(f"REUSE {s['name']} seed={seed}",flush=True)
        else:
            print(f"RUN {s['name']} seed={seed}: 200 queues, 2 C++ hosts, {sum(s['couriers'])} couriers, {config['count']} orders",flush=True)
            summary=run(root,config,timeout=timeout)
        quality='OK' if summary['p95_injection_lag_seconds']<=SPEC['max_acceptable_p95_lag_seconds'] else 'LAGGED'
        print(f"DONE {s['name']} seed={seed}: delivered={summary['delivered']} dropped={summary['dropped']} "
              f"latency={summary['mean_latency_seconds']:.4f}s throughput={summary['throughput_orders_per_second']:.2f}/s "
              f"p95 injection lag={summary['p95_injection_lag_seconds']:.4f}s [{quality}]",flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collect',action='store_true');parser.add_argument('--report',action='store_true')
    parser.add_argument('--archive',action='store_true');parser.add_argument('--output',type=Path)
    parser.add_argument('--smoke',action='store_true',help='baseline only, 5000 orders, seed 1; requires --output')
    parser.add_argument('--count',type=int);parser.add_argument('--seeds',type=int,nargs='+')
    parser.add_argument('--timeout',type=float,default=600,help='per-run startup, generation, and drain deadline')
    args=parser.parse_args()
    if not any((args.collect,args.report,args.archive)):parser.error('choose --collect, --report, and/or --archive')
    configure(args.output,args.count,args.seeds,args.smoke)
    if args.collect:collect(args.timeout)
    if args.report:
        from report_study import report
        report()
    if args.archive:
        from archive_runs import archive
        archive()


if __name__=='__main__':
    sys.modules['run_study']=sys.modules[__name__]
    main()
