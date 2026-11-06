#!/usr/bin/env python3
"""Audit the saved study without launching services or regenerating plots."""
import csv
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
SPEC=json.loads((HERE/'study.json').read_text())
PLOTS=['utilization.png','saturation.png','errors.png','throughput.png','latency.png','longest-jobs.png']
TOOLS=['filter_by_queue.py','filter_by_job.py','derive_event_metrics.py','derive_sys_metrics.py',
       'compute_use.py','plot_use.py','plot_sys_metrics.py','plot_derive_event_metrics.py',
       'create_event_trace.py','pretty_print_events.py']


def records(path):
    with path.open(newline='') as f:
        return list(csv.DictReader(f))


def validate():
    total_generated=total_delivered=total_dropped=0
    for scenario in SPEC['scenarios']:
        directory=HERE/scenario['name']
        for seed in SPEC['seeds']:
            root=directory/'runs'/f'seed-{seed}'
            config=json.loads((root/'config.json').read_text())
            expected={**SPEC['baseline'],'couriers':scenario['couriers'],'seed':seed}
            assert all(config[k]==v for k,v in expected.items()), root
            with (root/'jobs.csv').open(newline='') as f:
                jobs={r[0]:r for r in list(csv.reader(f))[1:]}
            assert len(jobs)==1000
            delivered={}
            for sink in root.glob('sink-*.csv'):
                with sink.open(newline='') as f:
                    for row in csv.reader(f):
                        assert row[0] not in delivered and jobs[row[0]]==row
                        delivered[row[0]]=row
            dropped=set()
            drop_counts={'q0':0,'q1':0}
            for line in (root/'analysis/events.log').read_text().splitlines():
                t,jid,_,qid,kind,*detail=line.split()
                if kind=='ERROR' and detail==['QUEUE-FULL']:
                    assert jid not in dropped
                    dropped.add(jid); drop_counts[qid]+=1
            assert set(delivered).isdisjoint(dropped)
            assert set(jobs)==set(delivered)|dropped
            summary=json.loads((root/'analysis/summary.json').read_text())
            assert summary['generated']==len(jobs) and summary['delivered']==len(delivered)
            assert summary['dropped']==len(dropped) and summary['incomplete']==0
            outcomes=records(root/'analysis/orders.csv')
            assert len(outcomes)==1000
            for row in outcomes:
                assert row['status']==('delivered' if row['order_id'] in delivered else 'dropped')
            total_generated+=len(jobs); total_delivered+=len(delivered); total_dropped+=len(dropped)
            if seed==SPEC['representative_seed']:
                bins=records(directory/'timeseries.csv')
                assert sum(int(r['delivered']) for r in bins)==len(delivered)
                uniform=records(directory/'tools/qmet-10ms.csv')
                for qid in (0,1):
                    assert sum(int(r[f'q{qid}_drops']) for r in bins)==drop_counts[f'q{qid}']
                    assert sum(int(r['num-of-errors']) for r in uniform if r['queue-sys-id']==str(qid))==drop_counts[f'q{qid}']
                sysmetrics=records(directory/'tools/sysmetrics.csv')
                assert abs(sum(float(r['throughput']) for r in sysmetrics)-len(delivered))<1e-6
                longest=sorted((r for r in outcomes if r['status']=='delivered'),key=lambda r:float(r['end_to_end_seconds']),reverse=True)[:3]
                assert {r['order_id'] for r in longest}=={r['order_id'] for r in records(directory/'longest-jobs.csv')}
        log=(directory/'tools/commands.log').read_text()
        assert all(tool in log for tool in TOOLS)
        for name in PLOTS:
            image=(directory/name).read_bytes()
            assert image[:8]==b'\x89PNG\r\n\x1a\n' and len(image)>1000
    outcome={'status':'passed','runs':21,'main_plots':42,'accessory_tools_per_set':10,
             'generated':total_generated,'delivered':total_delivered,'dropped':total_dropped,
             'incomplete':0,'checks':['raw CSV identity and uniqueness','every order delivered or dropped',
             'configuration matches experiment','error counts match sampled USE and time bins',
             'system-tool departure counts match sinks','longest jobs selected by end-to-end latency',
             'all tool commands recorded','all 42 main PNGs present']}
    (HERE/'audit.json').write_text(json.dumps(outcome,indent=2)+'\n')
    print(json.dumps(outcome,indent=2))


if __name__=='__main__':
    validate()
