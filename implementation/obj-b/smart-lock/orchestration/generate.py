#!/usr/bin/env python3
"""Generate repeatable Docker Compose FIFO/gRPC deployments from HomeKey qconf.csv."""
import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
APP = HERE.parent

def build_yaml(config_path, output_path, transport):
    configs = list(csv.DictReader(config_path.open(newline='')))
    services = {}
    volumes = {'ipc': {}} if transport == 'fifo' else {}
    input_mount = f'{config_path.parent.resolve()}:/work/inputs:ro'
    output_mount = f'{output_path.resolve()}:/work/outputs'
    common = [input_mount, output_mount]
    if transport == 'fifo':
        common.append('ipc:/work/ipc')

    for cfg in configs:
        qid = cfg['Sys ID']
        stage = cfg['Server Function']
        edges = cfg['Edges out'].strip().strip('[]').replace(' ', '')
        outgoing = [x for x in edges.split(',') if x]
        slots = int(cfg['Num. Servers'])
        capacity = int(cfg['Queue Max Size'])
        base = {'image': 'homekey-objective-c:latest', 'volumes': common}
        services[f'function-{qid}'] = {**base, 'expose': ['50052'], 'command': [
            'function', '--transport', transport, '--queue', qid,
            '--stage', stage, '--slots', str(slots)]}
        services[f'queue-{qid}'] = {**base, 'expose': ['50051'], 'command': [
            'queue', '--transport', transport, '--queue', qid,
            '--edges', json.dumps(outgoing), '--capacity', str(capacity),
            '--slots', str(slots)]}

    services['producer'] = {
        'image': 'homekey-objective-c:latest',
        'profiles': ['run'],
        'volumes': common,
        'command': ['producer', '--transport', transport, '--timeout', '900'],
    }

    cfg = {'name': 'homekey-' + transport, 'services': services}

    if volumes:
        cfg['volumes'] = volumes

    # JSON is valid YAML, and Compose accepts it as a compose configuration
    destination = HERE / ('compose-' + transport + '.yaml')
    destination.write_text(json.dumps(cfg, indent=2) + '\n')
    return destination

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source-root', type=Path, help='Existing HomeKey firmware source directory')
    ap.add_argument('--config', type=Path, help='Existing Objective B generated inputs/qconf.csv')
    ap.add_argument('--run-name', default='obj-c-small')
    ap.add_argument('--queues', type=int, default=6)
    ap.add_argument('--mode', choices=['decomposed', 'monolithic'], default='decomposed')
    ap.add_argument('--passes', type=int, default=1)
    ap.add_argument('--seed', type=int, default=5113)
    ap.add_argument('--dist', choices=['fixed', 'uniform', 'normal', 'geometric', 'poisson'], default='poisson')
    ap.add_argument('--lam', type=float, default=2)
    ap.add_argument('--interval', default='0:9999')
    args = ap.parse_args()
    directory = HERE / 'data' / args.run_name
    inputs = (args.config.resolve().parent if args.config else directory / 'inputs')
    outputs = directory / 'outputs'
    
    if args.config:
        if not args.config.is_file():
            ap.error(f'Config does not exist: {args.config}')
        if not (inputs / 'jobs.jsonl').exists():
            ap.error(f'Need jobs.jsonl and workload files beside {args.config}')
    else:
        if not args.source_root:
            ap.error('Provide --source-root or --config')
        if inputs.exists():
            ap.error(f'{inputs} already exists. Choose a new --run-name')
        source = args.source_root.resolve(strict=True)
        cmd = [sys.executable, str(APP / 'workload-generator' / 'workload_generator.py'),
               '--source-root', str(source), '--out-dir', str(inputs),
               '--queues', str(args.queues), '--mode', args.mode,
               '--passes', str(args.passes), '--seed', str(args.seed),
               '--dist', args.dist, '--lam', str(args.lam), '--interval', args.interval]
        subprocess.run(cmd, check=True)
    outputs.mkdir(parents=True, exist_ok=True)
    for transport in ('fifo', 'grpc'):
        dest = build_yaml(inputs / 'qconf.csv', outputs, transport)
        print('Generated', dest)
    print('Inputs:', inputs)
    print('Outputs:', outputs)

if __name__ == '__main__':
    main()
