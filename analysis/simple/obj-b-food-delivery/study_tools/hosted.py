#!/usr/bin/env python3
"""Shared collection and CLI for the large and very-large hosted studies."""
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


def expand_spec(spec):
    """Expand the compact repeating regional layout into the runner's explicit arrays."""
    spec = copy.deepcopy(spec)
    layout = spec.pop('layout', None)
    if layout is None:
        return spec
    regions = layout['regions']
    shares = layout['restaurant_shares']
    weights = layout['region_weights']
    baseline = spec['baseline']
    baseline.update(
        restaurant_regions=[r for r in range(regions) for _ in shares],
        restaurant_weights=[weights[r % len(weights)] * share for r in range(regions) for share in shares],
        kitchen_workers=[layout['kitchen_workers']] * (regions * len(shares)),
        couriers=[layout['couriers']] * regions,
    )
    for scenario in spec['scenarios']:
        total = round(sum(baseline['couriers']) * (100 + scenario['change_percent']) / 100)
        workers, extra = divmod(total, regions)
        scenario['couriers'] = [workers + (r < extra) for r in range(regions)]
    return spec


class HostedStudy:
    def __init__(self, code):
        self.code = code.resolve()
        self.repo = next(p for p in self.code.parents if (p / 'implementation/obj-b.food-delivery').is_dir())
        self.app = self.repo / 'implementation/obj-b.food-delivery'
        sys.path.insert(0, str(self.app))
        self.default = expand_spec(json.loads((self.code / 'study.json').read_text()))
        self.configure()

    def configure(self,output=None,count=None,seeds=None,smoke=False):
        self.here=Path(output).expanduser().resolve() if output else self.code
        self.results=self.here/'results';self.runs=self.here/'runs'
        if (smoke or count is not None or seeds is not None) and self.here==self.code:
            raise ValueError('Use --output for pilot/count/seed overrides; preserve the canonical study.')
        saved=self.runs/'study.json'
        self.spec=json.loads(saved.read_text()) if saved.exists() else copy.deepcopy(self.default)
        requested=copy.deepcopy(self.spec)
        if smoke:
            requested=copy.deepcopy(self.default);requested['baseline']['count']=5000;requested['seeds']=[1]
            requested['scenarios']=[requested['scenarios'][0]]
        if count is not None:
            if count<1:raise ValueError('count must be positive')
            requested['baseline']['count']=count
        if seeds is not None:
            if not seeds or len(set(seeds))!=len(seeds):raise ValueError('provide distinct seeds')
            requested['seeds']=seeds
        requested['representative_seed']=requested['seeds'][0]
        if saved.exists() and requested!=self.spec:raise ValueError('saved experiment differs; use a fresh --output directory')
        self.spec=requested


    def configuration(self,s,seed):return {**self.spec['baseline'],'couriers':s['couriers'],'seed':seed}


    def collect(self,timeout=600):
        import grpc
        from run_hosted import run
        self.runs.mkdir(parents=True,exist_ok=True)
        binary=self.app/'cpp/build/food_host'
        files=[Path(__file__),self.code/'study.json',self.app/'run_hosted.py',self.app/'run_network.py',*sorted((self.app/'fd').glob('*.py')),
               self.app/'cpp/host.cpp',self.app/'cpp/common.hpp',self.app/'cpp/CMakeLists.txt',binary]
        hashes={str(p.relative_to(self.repo)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        hashes['experiment']=hashlib.sha256(json.dumps(self.spec,sort_keys=True).encode()).hexdigest()
        provenance=self.runs/'provenance.json'
        if provenance.exists():
            if json.loads(provenance.read_text())['sha256']!=hashes:raise RuntimeError('Collection inputs changed; use a new output directory.')
        else:
            provenance.write_text(json.dumps({'sha256':hashes,'platform':platform.platform(),'python':sys.version,
                'grpcio':grpc.__version__,'logical_cpus':os.cpu_count(),'backend':'hosted-async'},indent=2)+'\n')
        (self.runs/'study.json').write_text(json.dumps(self.spec,indent=2)+'\n')
        schedule=[]
        for seed in self.spec['seeds']:
            scenarios=list(self.spec['scenarios']);random.Random(5113+seed).shuffle(scenarios)
            if seed==self.spec['representative_seed']:scenarios.sort(key=lambda s:s['name']!='baseline')
            schedule.extend((s,seed) for s in scenarios)
        (self.runs/'schedule.json').write_text(json.dumps([{'scenario':s['name'],'seed':seed} for s,seed in schedule],indent=2)+'\n')
        for s,seed in schedule:
            root=self.runs/s['name']/f'seed-{seed}';config=self.configuration(s,seed);archive=root.with_suffix('.tar.gz')
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
                print(f"RUN {s['name']} seed={seed}: {len(config['restaurant_regions'])+len(config['couriers'])} queues, 2 C++ hosts, {sum(s['couriers'])} couriers, {config['count']} orders",flush=True)
                summary=run(root,config,timeout=timeout)
            quality='OK' if summary['p95_injection_lag_seconds']<=self.spec['max_acceptable_p95_lag_seconds'] else 'LAGGED'
            print(f"DONE {s['name']} seed={seed}: delivered={summary['delivered']} dropped={summary['dropped']} "
                  f"latency={summary['mean_latency_seconds']:.4f}s throughput={summary['throughput_orders_per_second']:.2f}/s "
                  f"p95 injection lag={summary['p95_injection_lag_seconds']:.4f}s [{quality}]",flush=True)


    def main(self):
        parser=argparse.ArgumentParser(description=self.default['description'])
        parser.add_argument('--collect',action='store_true');parser.add_argument('--report',action='store_true')
        parser.add_argument('--archive',action='store_true');parser.add_argument('--output',type=Path)
        parser.add_argument('--smoke',action='store_true',help='baseline only, 5000 orders, seed 1; requires --output')
        parser.add_argument('--count',type=int);parser.add_argument('--seeds',type=int,nargs='+')
        parser.add_argument('--timeout',type=float,default=600,help='per-run startup, generation, and drain deadline')
        args=parser.parse_args()
        if not any((args.collect,args.report,args.archive)):parser.error('choose --collect, --report, and/or --archive')
        self.configure(args.output,args.count,args.seeds,args.smoke)
        if args.collect:self.collect(args.timeout)
        if args.report:
            from .report import report_hosted
            report_hosted(self)
        if args.archive:
            from .files import archive
            archive(self.here,self.results,self.runs,self.spec)


