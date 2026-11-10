"""Audit saved evidence, plots, and derived counts without running the services."""
import json
import numpy as np
import pandas as pd
from PIL import Image
from run_study import HERE, RESULTS, RUNS, SPEC, configuration
from report_study import saved_run, measure, PLOTS


def validate():
    rows=[];plots=0
    recorded=pd.read_csv(RESULTS/'metrics-by-run.csv')
    for scenario in SPEC['scenarios']:
        out=RESULTS/scenario['name']
        for seed in SPEC['seeds']:
            with saved_run(scenario['name'],seed) as root:
                row,queues,regions,events,states,orders=measure(root,scenario,seed)
                config=configuration(scenario,seed)
                actual=json.loads((root/'config.json').read_text())
                assert all(actual[k]==v for k,v in config.items()), 'config mismatch'
                assert len(queues)==20 and len(regions)==5
                delivered_ids=set(orders.loc[orders.status=='delivered','order_id'])
                routed=events.merge(orders[['order_id','restaurant','region']],left_on='job',right_on='order_id',validate='many_to_one')
                assert len(routed)==len(events), 'unknown event job'
                assert ((routed.qid==routed.restaurant)|(routed.qid==15+routed.region)).all(), 'wrong queue route'
                phases=events[events.job.isin(delivered_ids)&events.kind.isin(['ARRIVE-VIA','ENTERS-SERVER','EXITS-SERVER','DEPART-VIA'])]
                counts=phases.groupby(['job','qid','kind']).size()
                assert len(counts)==len(delivered_ids)*8 and (counts==1).all(), 'missing or repeated phase'
                reference=recorded[(recorded.scenario==scenario['name'])&(recorded.seed==seed)].iloc[0]
                for key,value in row.items():
                    if isinstance(value,(float,int)):assert np.isclose(value,reference[key]), key
                    else:assert value==reference[key],key
                rows.append(row)
                if seed==SPEC['representative_seed']:
                    queue_bins=pd.read_csv(out/'queue-timeseries.csv');system=pd.read_csv(out/'system-timeseries.csv')
                    samples=pd.read_csv(out/'tools/qmet-10ms.csv');native=pd.read_csv(out/'tools/sysmetrics.csv')
                    assert system.delivered.sum()==native.throughput.sum()==row['delivered']
                    assert queue_bins.drops.sum()==samples['num-of-errors'].sum()==row['dropped']
                    assert set(queue_bins.qid)==set(samples['queue-sys-id'])==set(range(20))
                    assert queue_bins.utilization.between(-1e-9,1+1e-9).all()
                    expected=orders[orders.status=='delivered'].nlargest(3,'end_to_end_seconds').order_id.tolist()
                    assert pd.read_csv(out/'longest-jobs.csv').order_id.tolist()==expected
                    for q in queues:
                        bins=queue_bins[queue_bins.qid==q['qid']];dt=bins.end_seconds-bins.start_seconds
                        assert np.isclose(np.dot(bins.utilization,dt)/dt.sum(),q['utilization'])
                        assert np.isclose(np.dot(bins.waiting,dt)/dt.sum(),q['mean_waiting'])
                    commands=(out/'tools/commands.log').read_text()
                    for tool in ('filter-by-queue','filter-by-job','derive-event-metrics','derive-sys-metrics','compute-use','plot-use','plot-sys-metrics','plot-derive-event-metrics','create-event-trace','pretty-print-job-trace'):
                        assert '/'+tool+'/' in commands,tool
        for filename in PLOTS:
            with Image.open(out/filename) as picture:picture.verify()
            plots+=1
        print('AUDITED',scenario['name'],flush=True)
    frame=pd.DataFrame(rows)
    assert all(group.workload_sha256.nunique()==1 for _,group in frame.groupby('seed'))
    assert len(frame)==21 and plots==42 and frame.generated.sum()==210000
    result={'status':'passed','runs':len(rows),'main_plots':plots,'generated':int(frame.generated.sum()),
            'delivered':int(frame.delivered.sum()),'dropped':int(frame.dropped.sum()),'incomplete':int(frame.incomplete.sum()),
            'checks':['configuration','unaltered unique sink records','regional routes','exact delivered event phases',
                      'drained regional registries','queue worker bounds and final empty state','paired workload identity',
                      'recomputed run metrics','time-weighted plot integration','binned and accessory counts','longest-job selection','PNG integrity']}
    (RESULTS/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':validate()
