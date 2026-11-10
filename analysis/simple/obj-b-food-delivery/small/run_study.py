#!/usr/bin/env python3
"""Collect paired-seed runs and publish the small food-delivery study.

Use the application's .venv Python from the repository root. --collect runs
missing experiments; --report recomputes tables/plots from saved evidence.
Existing successful runs are reused only if their configuration matches.
"""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import subprocess
import sys

HERE = Path(__file__).resolve().parent
RESULTS = HERE / 'results'
RUNS = HERE / 'runs'
REPO = next(p for p in HERE.parents if (p / 'implementation/obj-b.food-delivery').is_dir())
APP = REPO / 'implementation/obj-b.food-delivery'
OBJ_A = REPO / 'implementation/obj-a'
sys.path.insert(0, str(APP))
from run_grpc import run
from fd.analysis import adapter

SPEC = json.loads((HERE / 'study.json').read_text())


def configuration(scenario, seed):
    return {**SPEC['baseline'], 'couriers': scenario['couriers'], 'seed': seed}


def fingerprint():
    files = [HERE / 'study.json', APP / 'run_grpc.py', APP / 'fd/dispatch.py',
             APP / 'fd/workload.py', APP / 'fd/order.py', APP / 'fd/analysis.py',
             APP / 'cpp/server.cpp', APP / 'cpp/CMakeLists.txt', APP / 'cpp/build/food_server']
    return {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def collect():
    RUNS.mkdir(exist_ok=True)
    provenance = RUNS / 'provenance.json'
    hashes = fingerprint()
    if provenance.exists():
        if json.loads(provenance.read_text())['sha256'] != hashes:
            raise RuntimeError('Study implementation/configuration changed; do not mix incompatible runs.')
    else:
        import grpc, google.protobuf
        provenance.write_text(json.dumps({
            'git_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
            'platform': platform.platform(), 'machine': platform.machine(),
            'python': sys.version, 'grpcio': grpc.__version__,
            'protobuf': google.protobuf.__version__, 'sha256': hashes,
            'note': 'Wall-clock local experiments. One scenario at a time; OS scheduling is not deterministic.'
        }, indent=2) + '\n')
    schedule = []
    for seed in SPEC['seeds']:
        scenarios = list(SPEC['scenarios'])
        random.Random(5113 + seed).shuffle(scenarios)
        # Baseline first for an early accounting check; remaining order is fixed/randomized.
        if seed == 1:
            scenarios.sort(key=lambda s: s['name'] != 'baseline')
        schedule += [(s, seed) for s in scenarios]
    (RUNS / 'schedule.json').write_text(json.dumps([
        {'scenario': s['name'], 'seed': seed} for s, seed in schedule], indent=2) + '\n')
    for s, seed in schedule:
        root = RUNS / s['name'] / f'seed-{seed}'
        config = configuration(s, seed)
        summary = root / 'analysis/summary.json'
        if summary.exists():
            actual = json.loads((root / 'config.json').read_text())
            if any(actual[k] != v for k, v in config.items()):
                raise RuntimeError(f'Configuration mismatch: {root}')
            result = json.loads(summary.read_text())
            if result['incomplete'] or result['generated'] != config['count']:
                raise RuntimeError(f'Invalid existing run: {root}')
        else:
            print(f"RUN {s['name']} seed={seed} kitchens=20 couriers={s['couriers']}", flush=True)
            result = run(root, config, 'stages', timeout=180)
        print(f"DONE {s['name']} seed={seed}: delivered={result['delivered']} dropped={result['dropped']} latency={result['mean_latency_seconds']:.4f}s throughput={result['throughput_orders_per_second']:.2f}/s", flush=True)


def read_events(path):
    events = []
    for line in path.read_text().splitlines():
        parts = line.split(maxsplit=5)
        events.append({'time': int(parts[0])/1000, 'job': parts[1], 'qid': parts[3],
                       'kind': parts[4], 'detail': parts[5] if len(parts) > 5 else ''})
    return events


def write_csv(path, rows):
    if not rows:
        raise ValueError(f'no rows for {path}')
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def measure(root, scenario, seed):
    import numpy as np
    import pandas as pd
    config = configuration(scenario, seed)
    analysis = root / 'analysis'
    summary = json.loads((analysis / 'summary.json').read_text())
    orders = pd.read_csv(analysis / 'orders.csv')
    events = read_events(analysis / 'events.log')
    finish = max(e['time'] for e in events)
    qmet = pd.read_csv(analysis / 'qmet.csv')
    if summary['generated'] != 1000 or summary['incomplete'] or summary['delivered'] + summary['dropped'] != 1000:
        raise RuntimeError(f'Incomplete accounting: {root}')
    delivery = {e['job']: e['time'] for e in events if e['kind'] == 'DEPART-VIA' and e['detail'] == 'sink'}
    # Pair original jobs with normalized events using the common raw-event origin.
    from lib.job import iter_jobs
    jobs = list(iter_jobs((root / 'jobs.csv').read_text().splitlines()))
    raw = adapter().load_records(root)
    origin = min(r.time for r in raw)
    arrivals = {j.job_id: j.arrival_time - origin for j in jobs}
    admitted = {e['job'] for e in events if e['qid'] == 'q0' and e['kind'] == 'ARRIVE-VIA'}
    drops = {(e['job'], e['qid']) for e in events if e['kind'] == 'ERROR' and e['detail'] == 'QUEUE-FULL'}
    if len(drops) != summary['dropped'] or len(delivery) != summary['delivered']:
        raise RuntimeError(f'Event/summary mismatch: {root}')
    latency = orders.loc[orders.status == 'delivered', 'end_to_end_seconds']
    row = {'scenario': scenario['name'], 'seed': seed, 'couriers': config['couriers'],
           'total_workers': config['couriers'] + config['kitchen_workers'],
           'generated': len(jobs), 'delivered': len(delivery), 'dropped': len(drops),
           'q0_dropped': sum(q == 'q0' for _,q in drops), 'q1_dropped': sum(q == 'q1' for _,q in drops),
           'loss_percent': 100*len(drops)/len(jobs),
           'throughput_orders_per_second': summary['throughput_orders_per_second'],
           'offered_rate_per_second': (len(jobs)-1)/(jobs[-1].arrival_time-jobs[0].arrival_time),
           'generation_seconds': jobs[-1].arrival_time-jobs[0].arrival_time,
           'observation_seconds': summary['observation_seconds'],
           'mean_latency_seconds': float(latency.mean()), 'p50_latency_seconds': float(latency.quantile(.5)),
           'p90_latency_seconds': float(latency.quantile(.9)), 'p95_latency_seconds': float(latency.quantile(.95)),
           'max_latency_seconds': float(latency.max()),
           'mean_pickup_wait_seconds': summary['mean_pickup_wait_seconds']}
    states = {}
    for qid, servers in [(0,config['kitchen_workers']), (1,config['couriers'])]:
        frame = qmet[qmet['queue-sys-id'] == qid].copy()
        # Last state at a timestamp applies until the next event; errors are impulses.
        group = frame.groupby('time-step', sort=True).last()
        times = group.index.to_numpy()/1000
        wait = group['num-jobs-in-queue'].to_numpy()
        busy = group['num-jobs-in-service'].to_numpy()
        dt = np.diff(np.r_[times, finish])
        if min(wait) < 0 or min(busy) < 0 or max(busy) > servers or wait[-1] or busy[-1]:
            raise RuntimeError(f'Invalid state reconstruction: {root} q{qid}')
        if int(frame['num-of-errors'].sum()) != row[f'q{qid}_dropped']:
            raise RuntimeError('Error count mismatch')
        row[f'q{qid}_utilization'] = float(np.dot(dt,busy)/(servers*finish))
        row[f'q{qid}_mean_waiting'] = float(np.dot(dt,wait)/finish)
        row[f'q{qid}_max_waiting'] = int(max(wait))
        states[qid] = (times, wait, busy, servers)
    state = json.loads((root / 'dispatch-state.json').read_text())
    if state['waiting'] or any(c['status'] != 'available' for c in state['couriers'].values()) or sum(c['completed'] for c in state['couriers'].values()) != len(delivery):
        raise RuntimeError('Dispatcher did not drain')
    # Verify identical requested service attributes across paired seeds/scenarios.
    row['workload_sha256'] = hashlib.sha256('\n'.join(j.job_id + '|' + '|'.join(f'{t.tid}:{t.wait_seconds}' for t in j.tasks) for j in jobs).encode()).hexdigest()
    return row, events, states, arrivals, delivery, orders


def tool(script, args, log):
    command = [sys.executable, str(OBJ_A/script), *map(str,args)]
    log.write('$ ' + ' '.join(command) + '\n')
    log.flush()
    subprocess.run(command, stdout=log, stderr=log, check=True)


def accessory(root, out, longest):
    """Exercise all ten accessory CLIs with explicit, retained inputs/outputs."""
    out.mkdir(parents=True,exist_ok=True)
    a = root / 'analysis'
    with (out/'commands.log').open('w') as log:
        tool('filter-by-queue/filter_by_queue.py', ['--qmet',a/'qmet.csv','--qid','q0','--out',out/'q0-qmet.csv','-v'],log)
        tool('filter-by-queue/filter_by_queue.py', ['--qmet',a/'qmet.csv','--qid','q1','--out',out/'q1-qmet.csv','-v'],log)
        tool('derive-event-metrics/derive_event_metrics.py',['--events',a/'events.log','--out',out/'emetrics.csv','-v'],log)
        tool('derive-sys-metrics/derive_sys_metrics.py',['--events',a/'events.log','--out',out/'sysmetrics.csv','--window','500','--dense','-v'],log)
        # Uniform 10ms states make the shared rolling mean a time-sampled average.
        tool('compute-use/compute_use.py',['--qmet',out/'qmet-10ms.csv','--config',a/'qconf.csv','--out',out/'quse.csv','-v'],log)
        tool('plot-use/plot_use.py',['--quse',out/'quse.csv','--out',out/'use.png','--title','Uniform 10 ms samples; windows 0.1/1/10 s'],log)
        tool('plot-sys-metrics/plot_sys_metrics.py',['--sysmetrics',out/'sysmetrics.csv','--out',out/'system.png','--title','500 ms departure bins; native throughput is count/bin'],log)
        tool('plot-derive-event-metrics/plot_derive_event_metrics.py',[out/'emetrics.csv',out/'visit-hist.png','--hist','turnaround-time','--bins','20','--title','Per-queue visit duration (ms)'],log)
        tool('plot-derive-event-metrics/plot_derive_event_metrics.py',[out/'emetrics.csv',out/'visit-scatter.png','--scat','arrival-time,turnaround-time','--title','Per-queue visit duration by arrival (ms)'],log)
        tool('filter-by-job/filter_by_job.py',['--events',a/'events.log','--jobs',','.join(longest),'--out',out/'longest-jobs.log'],log)
        tool('pretty-print-job-trace/pretty_print_events.py',[out/'longest-jobs.log','--out',out/'longest-jobs-pretty.log','--no-color'],log)
        tool('pretty-print-job-trace/pretty_print_events.py',[a/'events.log','--out',out/'all-events-pretty.log','--no-color'],log)
        tool('create-event-trace/create_event_trace.py',['--events',out/'longest-jobs.log','--out',out/'longest-event-trace.png','-v'],log)


def plot_set(scenario, root, data):
    import numpy as np
    import pandas as pd
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    row, events, states, arrivals, delivered, orders = data
    out = RESULTS/scenario['name']
    tools = out/'tools'
    tools.mkdir(parents=True,exist_ok=True)
    finish = max(e['time'] for e in events)
    width = SPEC['bin_seconds']
    edges = np.arange(0, finish+width, width)
    if edges[-1] < finish: edges = np.r_[edges,finish]
    # Report bins keep service state time-weighted and count each departure once.
    rows = []
    for left,right in zip(edges[:-1],edges[1:]):
        r={'start_seconds':float(left),'end_seconds':float(min(right,finish))}
        actual = min(right,finish)-left
        for qid,(times,wait,busy,servers) in states.items():
            dt = np.maximum(0,np.minimum(np.r_[times[1:],finish],min(right,finish))-np.maximum(times,left))
            r[f'q{qid}_utilization'] = float(np.dot(dt,busy)/(servers*actual))
            r[f'q{qid}_waiting'] = float(np.dot(dt,wait)/actual)
            errors=sum(e['qid']==f'q{qid}' and e['kind']=='ERROR' and e['detail']=='QUEUE-FULL' and left <= e['time'] and (e['time'] < right or right >= finish and e['time'] <= finish) for e in events)
            r[f'q{qid}_drops'] = errors
            r[f'q{qid}_drop_rate'] = errors/actual
        completions=[(j,t) for j,t in delivered.items() if left <= t and (t < right or right >= finish and t <= finish)]
        r['delivered'] = len(completions)
        r['throughput_per_second'] = len(completions)/actual
        lat=np.array([t-arrivals[j] for j,t in completions])
        for label,p in [('p10',10),('median',50),('p90',90)]:
            r[f'{label}_latency_seconds']=float(np.percentile(lat,p)) if len(lat) else math.nan
        rows.append(r)
    if sum(r['delivered'] for r in rows) != row['delivered'] or any(sum(r[f'q{q}_drops'] for r in rows) != row[f'q{q}_dropped'] for q in (0,1)):
        raise RuntimeError('Time bins do not reconcile with completed/dropped counts')
    write_csv(out/'timeseries.csv',rows)
    frame=pd.DataFrame(rows)
    # Save a uniform-time input for the existing USE CLI (not event-sample averages).
    sampled=[]
    for qid,(times,wait,busy,servers) in states.items():
        error_times=np.array([round(e['time']*1000) for e in events if e['qid']==f'q{qid}' and e['kind']=='ERROR'])
        for step in range(0,int(math.ceil(finish*1000/10))*10+1,10):
            t=step/1000
            index=max(0,np.searchsorted(times,t,side='right')-1)
            # Error impulses occur once in the right-closed 10ms bucket.
            errors=int(sum((error_times > step-10) & (error_times <= step)))
            sampled.append({'time-step':step,'queue-sys-id':qid,'num-jobs-in-queue':int(wait[index]),
                            'num-jobs-in-service':int(busy[index]),'num-of-errors':errors})
    sampled.sort(key=lambda r:(r['time-step'],r['queue-sys-id']))
    write_csv(tools/'qmet-10ms.csv',sampled)
    longest=orders[orders.status=='delivered'].nlargest(3,'end_to_end_seconds')['order_id'].tolist()
    orders[orders.order_id.isin(longest)].sort_values('end_to_end_seconds',ascending=False).to_csv(out/'longest-jobs.csv',index=False)
    breakdown=[]
    for jid in longest:
        r={'order_id':jid,'end_to_end_seconds':delivered[jid]-arrivals[jid]}
        for qid in (0,1):
            ev=[e for e in events if e['job']==jid and e['qid']==f'q{qid}']
            stamps={e['kind']:e['time'] for e in ev}
            r[f'q{qid}_wait_seconds']=stamps['ENTERS-SERVER']-stamps['ARRIVE-VIA']
            r[f'q{qid}_service_seconds']=stamps['EXITS-SERVER']-stamps['ENTERS-SERVER']
        r['transfer_and_output_seconds']=r['end_to_end_seconds']-sum(r[k] for k in r if k.startswith('q'))
        breakdown.append(r)
    write_csv(out/'longest-job-breakdown.csv',breakdown)
    accessory(root,tools,longest)
    plt.rcParams.update({'font.size':11,'axes.spines.top':False,'axes.spines.right':False})
    colors=['#176A8A','#C66A19']
    title=f"{scenario['name']} | 20 kitchens, {scenario['couriers']} couriers | seed 1"
    def new(label):
        fig,ax=plt.subplots(figsize=(10,4.8))
        ax.set_title(title+'\n'+label,loc='left',fontweight='bold',fontsize=12)
        ax.set_xlabel('Seconds from first event')
        ax.grid(alpha=.18)
        return fig,ax
    def save(fig,name):
        fig.tight_layout()
        fig.savefig(out/name,dpi=150)
        plt.close(fig)
    for key,label,name in [('utilization','Time-weighted busy-worker fraction','utilization.png'),
                           ('waiting','Time-weighted waiting orders','saturation.png'),
                           ('drop_rate','Queue-full rejection rate (orders/s)','errors.png')]:
        fig,ax=new(label+'; 0.5 s bins')
        for qid,color in enumerate(colors):
            ax.stairs(frame[f'q{qid}_{key}'],np.r_[frame.start_seconds,frame.end_seconds.iloc[-1]],label=f'q{qid}: '+('kitchen' if qid==0 else 'pickup'),color=color,lw=2)
        ax.set_ylabel(label)
        ceiling={'utilization':1.05,'waiting':70,'drop_rate':60}[key]
        ax.set_ylim(0,max(ceiling,1.05*max(frame[f'q0_{key}'].max(),frame[f'q1_{key}'].max())))
        if key=='drop_rate' and row['dropped']==0:
            ax.text(.5,.5,'No queue-full rejections in this run',transform=ax.transAxes,ha='center',color='#6A7180')
        ax.legend(frameon=False)
        save(fig,name)
    fig,ax=new('Delivered throughput; 0.5 s bins')
    ax.stairs(frame.throughput_per_second,np.r_[frame.start_seconds,frame.end_seconds.iloc[-1]],color='#23764C',lw=2,label='Delivered orders/s')
    ax.axhline(row['offered_rate_per_second'],ls='--',color='#6A7180',label='Measured mean offered rate')
    ax.set_ylabel('Orders per second'); ax.set_ylim(0,max(160,1.05*frame.throughput_per_second.max())); ax.legend(frameon=False)
    save(fig,'throughput.png')
    fig,ax=new('Delivered-order latency by completion bin')
    center=(frame.start_seconds+frame.end_seconds)/2
    ax.fill_between(center,frame.p10_latency_seconds,frame.p90_latency_seconds,color='#176A8A',alpha=.18,label='P10–P90 within bin')
    ax.plot(center,frame.median_latency_seconds,color='#176A8A',lw=2,label='Median within bin')
    ax.set_ylabel('Generation-to-delivery seconds'); ax.set_ylim(0,max(1.8,1.05*frame.p90_latency_seconds.max())); ax.legend(frameon=False)
    save(fig,'latency.png')
    fig,ax=plt.subplots(figsize=(11,5))
    ax.set_title(title+'\nThree longest delivered orders: actual timeline',loc='left',fontweight='bold',fontsize=12)
    labels=[]
    from matplotlib.patches import Patch
    for index,jid in enumerate(longest):
        ev=[e for e in events if e['job']==jid]
        arrival=arrivals[jid]
        end=delivered[jid]
        ax.plot([arrival,end],[index,index],color='#8B939B',lw=2,zorder=1)
        for qid,color in enumerate(colors):
            subset=[e for e in ev if e['qid']==f'q{qid}']
            at=next(e['time'] for e in subset if e['kind']=='ARRIVE-VIA')
            enter=next(e['time'] for e in subset if e['kind']=='ENTERS-SERVER')
            leave=next(e['time'] for e in subset if e['kind']=='EXITS-SERVER')
            ax.barh(index,enter-at,left=at,height=.34,color=color,alpha=.25)
            ax.barh(index,leave-enter,left=enter,height=.34,color=color)
        ax.scatter([arrival,end],[index,index],color='#222D38',s=25,zorder=3)
        labels.append(f'{jid}\n{end-arrival:.3f} s')
    ax.set_yticks(range(len(labels)),labels); ax.invert_yaxis()
    first=min(arrivals[j] for j in longest); last=max(delivered[j] for j in longest)
    ax.set_xlim(first-(last-first)*.03,last+(last-first)*.03)
    ax.set_xlabel('Seconds from first event'); ax.grid(axis='x',alpha=.18)
    ax.legend(handles=[Patch(color=colors[0],label='Kitchen service'),Patch(color=colors[1],label='Delivery service'),Patch(color='#888888',alpha=.25,label='Pale bars: queue wait')],loc='upper center',bbox_to_anchor=(.5,-.18),ncol=3,frameon=False)
    save(fig,'longest-jobs.png')


def report():
    RESULTS.mkdir(exist_ok=True)
    import pandas as pd
    rows=[]
    for scenario in SPEC['scenarios']:
        for seed in SPEC['seeds']:
            root=RUNS/scenario['name']/f'seed-{seed}'
            data=measure(root,scenario,seed)
            rows.append(data[0])
            if seed==SPEC['representative_seed']:
                plot_set(scenario,root,data)
        print(f"PLOTS {scenario['name']}",flush=True)
    table=pd.DataFrame(rows)
    for seed, group in table.groupby('seed'):
        if group.workload_sha256.nunique()!=1:
            raise RuntimeError(f'Service workload differs for paired seed {seed}')
    table.to_csv(RESULTS/'metrics-by-run.csv',index=False)
    numeric=[c for c in table.columns if c not in ('scenario','seed','workload_sha256')]
    aggregate=table.groupby('scenario',sort=False)[numeric].agg(['mean','min','max'])
    aggregate.columns=['_'.join(c) for c in aggregate.columns]
    aggregate.to_csv(RESULTS/'metrics-by-scenario.csv')
    comparisons=[]
    for s in SPEC['scenarios']:
        g=table[table.scenario==s['name']].set_index('seed')
        base=table[table.scenario=='baseline'].set_index('seed')
        comparisons.append({'scenario':s['name'],'couriers':s['couriers'],'courier_change_percent':s['change_percent'],
            'total_worker_change_percent':100*((20+s['couriers'])/40-1),
            'mean_throughput':g.throughput_orders_per_second.mean(),
            'paired_throughput_change_percent':((g.throughput_orders_per_second/base.throughput_orders_per_second-1)*100).mean(),
            'mean_latency_seconds':g.mean_latency_seconds.mean(),
            'paired_latency_change_percent':((g.mean_latency_seconds/base.mean_latency_seconds-1)*100).mean(),
            'mean_p95_seconds':g.p95_latency_seconds.mean(),'mean_loss_percent':g.loss_percent.mean(),
            'mean_q0_utilization':g.q0_utilization.mean(),'mean_q1_utilization':g.q1_utilization.mean(),
            'mean_q0_waiting':g.q0_mean_waiting.mean(),'mean_q1_waiting':g.q1_mean_waiting.mean()})
    write_csv(RESULTS/'comparison.csv',comparisons)
    comparison_plot(table)
    # Audit every required set and all retained raw accounting.
    required=['utilization.png','saturation.png','errors.png','throughput.png','latency.png','longest-jobs.png']
    manifest=[]
    for scenario in SPEC['scenarios']:
        for filename in required:
            path=RESULTS/scenario['name']/filename
            if not path.is_file() or not path.stat().st_size: raise RuntimeError(f'Missing {path}')
            manifest.append({'scenario':scenario['name'],'plot':filename,'path':str(path.relative_to(RESULTS))})
    write_csv(RESULTS/'plot-manifest.csv',manifest)
    (RESULTS/'validation.json').write_text(json.dumps({'runs':len(rows),'scenarios':7,'main_plots':len(manifest),
        'orders_generated':int(table.generated.sum()),'orders_delivered':int(table.delivered.sum()),
        'orders_dropped':int(table.dropped.sum()),'incomplete':0,'paired_workloads_match':True,
        'all_dispatchers_drained':True,'all_accessory_commands_succeeded':True},indent=2)+'\n')
    print(pd.DataFrame(comparisons).to_string(index=False),flush=True)


def comparison_plot(table):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,3,figsize=(14,4.6))
    for ax,column,label in zip(axes,['throughput_orders_per_second','mean_latency_seconds','loss_percent'],
                               ['Delivered orders/s','Mean delivered latency (s)','Lost orders (%)']):
        group=table.groupby('couriers')[column].agg(['mean','min','max']).sort_index()
        ax.errorbar(group.index,group['mean'],yerr=[group['mean']-group['min'],group['max']-group['mean']],fmt='o-',capsize=4,color='#176A8A')
        ax.axvline(20,color='#888888',ls='--',lw=1)
        ax.set_xlabel('Courier workers (kitchens fixed at 20)'); ax.set_ylabel(label)
        ax.grid(alpha=.18); ax.set_ylim(bottom=0)
    fig.suptitle('Two-queue sensitivity study | mean and min–max across three paired seeds',fontweight='bold')
    fig.tight_layout(); fig.savefig(RESULTS/'sensitivity-comparison.png',dpi=150); plt.close(fig)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--collect',action='store_true')
    p.add_argument('--report',action='store_true')
    args=p.parse_args()
    if not args.collect and not args.report: p.error('choose --collect and/or --report')
    os.environ.setdefault('MPLBACKEND','Agg')
    if args.collect: collect()
    if args.report: report()
