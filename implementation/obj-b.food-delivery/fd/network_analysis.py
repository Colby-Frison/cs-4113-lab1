"""Topology-aware accounting for restaurant queues feeding regional pickup queues."""
import csv
import json
import statistics
from pathlib import Path

from fd.analysis import adapter
from fd.order import validate_order
from lib.job import iter_jobs


def analyze_network(root, config):
    root = Path(root)
    bridge = adapter()
    n = len(config['restaurant_regions'])
    records = bridge.load_records(root)
    for region in range(len(config['couriers'])):
        directory = root / f'region-{region}'
        # Short pilots can legitimately send no orders to a region.
        if any(path.stat().st_size for path in directory.glob('*.log')):
            records.extend(bridge.load_records(directory))
    jobs = list(iter_jobs((root / 'jobs.csv').read_text().splitlines()))
    generated = {j.job_id:j for j in jobs}
    if len(generated) != len(jobs):
        raise ValueError('duplicate generated IDs')
    for record in records:
        if record.job not in generated:
            raise ValueError('unknown event job')
        restaurant = validate_order(generated[record.job]).restaurant
        regional_q = n + config['restaurant_regions'][restaurant]
        if record.qsid not in (f'q{restaurant}', f'q{regional_q}'):
            raise ValueError('order visited incorrect restaurant/region')
        if record.kind == 'ARRIVE-VIA':
            record.rest = 'source' if record.qsid == f'q{restaurant}' else f'q{restaurant}'
        elif record.kind == 'DEPART-VIA':
            record.rest = f'q{regional_q}' if record.qsid == f'q{restaurant}' else 'sink'
    def rank(r):
        if r.kind in ('TASK-START','TASK-END'):
            return 2+int(r.rest.split()[0])*2+(r.kind=='TASK-END')
        return {'ARRIVE-VIA':0,'ENTERS-SERVER':1,'EXITS-SERVER':100,'DEPART-VIA':101}.get(r.kind,102)
    records.sort(key=lambda r:(r.time,r.job,int(r.qsid[1:]),rank(r),r.order))
    output=root/'analysis'; output.mkdir()
    bridge.write_events(records,output/'events.log')
    bridge.write_qmet(records,output/'qmet.csv')
    specs={f'q{i}':(config['kitchen_capacity'],workers) for i,workers in enumerate(config['kitchen_workers'])}
    specs.update({f'q{n+i}':(config['pickup_capacity'],workers) for i,workers in enumerate(config['couriers'])})
    bridge.write_qconf(records,output/'qconf.csv',specs)
    delivered={}
    for path in root.glob('region-*/sink-*.csv'):
        region=int(path.parent.name.split('-')[1])
        for job in iter_jobs(path.read_text().splitlines()):
            if job.job_id in delivered or job.job_id not in generated or job.raw_line!=generated[job.job_id].raw_line:
                raise ValueError('unknown, duplicate, or changed delivered order')
            if config['restaurant_regions'][validate_order(job).restaurant]!=region:
                raise ValueError('delivery in wrong region')
            delivered[job.job_id]=job
    dropped={r.job:r.qsid for r in records if r.kind=='ERROR' and r.rest=='QUEUE-FULL'}
    completed={r.job:r.time for r in records if r.kind=='DEPART-VIA' and r.rest=='sink'}
    if set(completed)!=set(delivered) or set(dropped)&set(delivered):
        raise ValueError('completion/drop evidence disagrees')
    rows=[]
    for job in jobs:
        restaurant=validate_order(job).restaurant
        rows.append({'order_id':job.job_id,'restaurant':restaurant,'region':config['restaurant_regions'][restaurant],
                     'status':'delivered' if job.job_id in delivered else 'dropped' if job.job_id in dropped else 'incomplete',
                     'drop_queue':dropped.get(job.job_id,''),
                     'end_to_end_seconds':max(0,completed[job.job_id]-job.arrival_time) if job.job_id in delivered else ''})
    with (output/'orders.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0])); writer.writeheader();writer.writerows(rows)
    lat=[r['end_to_end_seconds'] for r in rows if r['status']=='delivered']
    elapsed=max(r.time for r in records)-min(j.arrival_time for j in jobs)
    summary={'mode':'regional-network','queues':len(specs),'generated':len(jobs),'delivered':len(delivered),
             'dropped':len(dropped),'incomplete':sum(r['status']=='incomplete' for r in rows),
             'mean_latency_seconds':statistics.mean(lat) if lat else None,
             'throughput_orders_per_second':len(delivered)/elapsed if elapsed>0 else None,
             'observation_seconds':elapsed}
    for region in range(len(config['couriers'])):
        state=json.loads((root/f'region-{region}'/'dispatch-state.json').read_text())
        count=sum(r['status']=='delivered' and r['region']==region for r in rows)
        if state['waiting'] or any(c['status']!='available' for c in state['couriers'].values()) or sum(c['completed'] for c in state['couriers'].values())!=count:
            raise ValueError('regional registry did not drain or completion count disagrees')
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    if summary['incomplete']: raise RuntimeError('network has incomplete orders')
    return summary
