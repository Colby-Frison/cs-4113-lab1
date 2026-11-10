"""Time-weighted multi-queue metrics, six-plot sets, and accessory-tool evidence."""
from contextlib import contextmanager
import csv
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from run_study import HERE, RESULTS, RUNS, REPO, APP, SPEC, configuration
OBJ_A=REPO/'implementation/obj-a'
PLOTS=['utilization.png','saturation.png','errors.png','throughput.png','latency.png','longest-jobs.png']
COLORS=['#176A8A','#C66A19','#398557','#985DA8','#B54B56']


@contextmanager
def saved_run(scenario,seed):
    root=RUNS/scenario/f'seed-{seed}'
    if root.is_dir():
        yield root
    else:
        archive=root.with_suffix('.tar.gz')
        with tempfile.TemporaryDirectory(prefix='food-medium-report-') as temp:
            with tarfile.open(archive) as tar:tar.extractall(temp,filter='data')
            yield Path(temp)/root.name


def write_csv(path,rows):
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def events_frame(path):
    rows=[]
    for line in path.read_text().splitlines():
        t,jid,_,qid,kind,*rest=line.split(maxsplit=5)
        rows.append((int(t)/1000,jid,int(qid[1:]),kind,rest[0] if rest else ''))
    return pd.DataFrame(rows,columns=['time','job','qid','kind','detail'])


def measure(root,scenario,seed):
    config=configuration(scenario,seed);n=len(config['restaurant_regions']);nq=n+len(config['couriers'])
    summary=json.loads((root/'analysis/summary.json').read_text())
    orders=pd.read_csv(root/'analysis/orders.csv');events=events_frame(root/'analysis/events.log')
    qmet=pd.read_csv(root/'analysis/qmet.csv')
    with (root/'jobs.csv').open(newline='') as f:jobs=list(csv.reader(f))[1:]
    if len(jobs)!=config['count'] or summary['incomplete'] or summary['delivered']+summary['dropped']!=len(jobs):
        raise RuntimeError('incomplete accounting')
    if len({j[0] for j in jobs})!=len(jobs):raise RuntimeError('duplicate source IDs')
    # Check all sinks against original full CSV records and regional ownership.
    original={j[0]:j for j in jobs};seen=set()
    for path in root.glob('region-*/sink-*.csv'):
        with path.open(newline='') as f:
            for j in csv.reader(f):
                if j[0] in seen or original.get(j[0])!=j:raise RuntimeError('sink row identity mismatch')
                restaurant=int(j[0].split('.')[1][1:]);region=int(path.parent.name.split('-')[1])
                if config['restaurant_regions'][restaurant]!=region:raise RuntimeError('cross-region delivery')
                seen.add(j[0])
    delivered=orders[orders.status=='delivered']
    drops=events[(events.kind=='ERROR')&(events.detail=='QUEUE-FULL')]
    if len(seen)!=len(delivered) or len(drops)!=summary['dropped'] or set(seen)&set(drops.job) or set(original)!=set(seen)|set(drops.job):
        raise RuntimeError('loss/delivery accounting mismatch')
    finish=float(events.time.max());generation=float(jobs[-1][1])-float(jobs[0][1])
    states={};queue_rows=[]
    servers=config['kitchen_workers']+config['couriers']
    for qid in range(nq):
        frame=qmet[qmet['queue-sys-id']==qid]
        if frame.empty:raise RuntimeError(f'unvisited queue {qid}')
        group=frame.groupby('time-step',sort=True).last()
        times=group.index.to_numpy()/1000;waiting=group['num-jobs-in-queue'].to_numpy();busy=group['num-jobs-in-service'].to_numpy()
        dt=np.diff(np.r_[times,finish]);error_count=int(frame['num-of-errors'].sum())
        if min(waiting)<0 or min(busy)<0 or max(busy)>servers[qid] or waiting[-1] or busy[-1]:raise RuntimeError('invalid queue reconstruction')
        if error_count!=sum(drops.qid==qid):raise RuntimeError('queue loss mismatch')
        states[qid]=(times,waiting,busy,servers[qid])
        queue_rows.append({'scenario':scenario['name'],'seed':seed,'qid':qid,'type':'kitchen' if qid<n else 'pickup',
            'region':config['restaurant_regions'][qid] if qid<n else qid-n,'servers':servers[qid],
            'utilization':float(np.dot(dt,busy)/(servers[qid]*finish)),'mean_waiting':float(np.dot(dt,waiting)/finish),
            'max_waiting':int(max(waiting)),'dropped':error_count})
    regional=[]
    for region in range(len(config['couriers'])):
        subset=orders[orders.region==region];good=subset[subset.status=='delivered']
        state=json.loads((root/f'region-{region}'/'dispatch-state.json').read_text())
        if state['waiting'] or any(c['status']!='available' for c in state['couriers'].values()) or sum(c['completed'] for c in state['couriers'].values())!=len(good):
            raise RuntimeError('regional dispatcher mismatch')
        regional.append({'scenario':scenario['name'],'seed':seed,'region':region,'couriers':config['couriers'][region],
            'generated':len(subset),'delivered':len(good),'loss_percent':100*(len(subset)-len(good))/len(subset),
            'throughput_per_second':len(good)/summary['observation_seconds'],
            'mean_latency_seconds':good.end_to_end_seconds.mean(),'p95_latency_seconds':good.end_to_end_seconds.quantile(.95),
            'offered_rate_per_second':len(subset)/generation})
    row={'scenario':scenario['name'],'seed':seed,'queues':nq,'couriers':sum(config['couriers']),'total_workers':sum(servers),
        'generated':len(jobs),'delivered':len(delivered),'dropped':len(drops),'incomplete':0,
        'kitchen_drops':int(sum(drops.qid<n)),'pickup_drops':int(sum(drops.qid>=n)),
        'loss_percent':100*len(drops)/len(jobs),'throughput_orders_per_second':summary['throughput_orders_per_second'],
        'mean_latency_seconds':delivered.end_to_end_seconds.mean(),'p50_latency_seconds':delivered.end_to_end_seconds.quantile(.5),
        'p95_latency_seconds':delivered.end_to_end_seconds.quantile(.95),'max_latency_seconds':delivered.end_to_end_seconds.max(),
        'offered_rate_per_second':(len(jobs)-1)/generation,'generation_seconds':generation,'observation_seconds':summary['observation_seconds'],
        'workload_sha256':hashlib.sha256('\n'.join(j[0]+'|'+j[2] for j in jobs).encode()).hexdigest()}
    return row,queue_rows,regional,events,states,orders


def bins_and_samples(data):
    row,queue_rows,regional,events,states,orders=data
    finish=float(events.time.max());width=SPEC['bin_seconds']
    edges=np.r_[np.arange(0,finish,width),finish]
    if len(edges)<2:edges=np.array([0,finish])
    duration=np.diff(edges);left=edges[:-1];right=edges[1:]
    nq=len(states);matrices={name:np.zeros((nq,len(left))) for name in ['utilization','waiting','drop_rate']}
    rows=[];uniform=[]
    drops=events[events.kind=='ERROR']
    for qid,(times,waiting,busy,servers) in states.items():
        spans=np.maximum(0,np.minimum(np.r_[times[1:],finish][:,None],right)-np.maximum(times[:,None],left))
        matrices['utilization'][qid]=busy@spans/(servers*duration)
        matrices['waiting'][qid]=waiting@spans/duration
        error_times=drops[drops.qid==qid].time.to_numpy()
        counts=np.histogram(error_times,bins=edges)[0]
        matrices['drop_rate'][qid]=counts/duration
        # Exact integer-millisecond error bucketing, sampled states every 10 ms.
        milliseconds=np.rint(error_times*1000).astype(int)
        for step in range(0,math.ceil(finish*100)*10+1,10):
            index=max(0,np.searchsorted(times,step/1000,side='right')-1)
            uniform.append({'time-step':step,'queue-sys-id':qid,'num-jobs-in-queue':int(waiting[index]),
                'num-jobs-in-service':int(busy[index]),'num-of-errors':int(sum((milliseconds>step-10)&(milliseconds<=step)))})
        for i in range(len(left)):
            rows.append({'qid':qid,'start_seconds':left[i],'end_seconds':right[i],'utilization':matrices['utilization'][qid,i],
                'waiting':matrices['waiting'][qid,i],'drops':int(counts[i]),'drop_rate':matrices['drop_rate'][qid,i]})
    completions=events[(events.kind=='DEPART-VIA')&(events.detail=='sink')].merge(
        orders[['order_id','end_to_end_seconds','region']],left_on='job',right_on='order_id')
    counts=np.histogram(completions.time,bins=edges)[0]
    if sum(counts)!=row['delivered'] or sum(r['drops'] for r in rows)!=row['dropped']:raise RuntimeError('binned accounting mismatch')
    system=[]
    for i in range(len(left)):
        selected=completions[(completions.time>=left[i])&((completions.time<right[i])|((i==len(left)-1)&(completions.time<=right[i])))]
        lat=selected.end_to_end_seconds
        r={'start_seconds':left[i],'end_seconds':right[i],'delivered':int(counts[i]),'throughput_per_second':counts[i]/duration[i],
            'p10_latency_seconds':lat.quantile(.1),'median_latency_seconds':lat.quantile(.5),'p90_latency_seconds':lat.quantile(.9)}
        for region in range(5):
            subset=selected[selected.region==region]
            r[f'region{region}_throughput']=len(subset)/duration[i]
            r[f'region{region}_median_latency']=subset.end_to_end_seconds.median()
        system.append(r)
    uniform.sort(key=lambda r:(r['time-step'],r['queue-sys-id']))
    return edges,matrices,rows,system,uniform,completions


def tool(script,args,log):
    command=[sys.executable,str(OBJ_A/script),*map(str,args)]
    log.write('$ '+' '.join(command)+'\n');log.flush()
    subprocess.run(command,stdout=log,stderr=log,check=True)


def accessory(root,out,longest):
    a=root/'analysis'
    with (out/'commands.log').open('w') as log:
        for qid in (0,15):tool('filter-by-queue/filter_by_queue.py',['--qmet',a/'qmet.csv','--qid',f'q{qid}','--out',out/f'q{qid}-qmet.csv','-v'],log)
        tool('derive-event-metrics/derive_event_metrics.py',['--events',a/'events.log','--out',out/'emetrics.csv','-v'],log)
        tool('derive-sys-metrics/derive_sys_metrics.py',['--events',a/'events.log','--window','1000','--dense','--out',out/'sysmetrics.csv','-v'],log)
        tool('compute-use/compute_use.py',['--qmet',out/'qmet-10ms.csv','--config',a/'qconf.csv','--out',out/'quse.csv','-v'],log)
        # Show all five regional pickup queues in the shared USE plot.
        quse=pd.read_csv(out/'quse.csv');quse[quse['queue-sys-id']>=15].to_csv(out/'regional-quse.csv',index=False)
        tool('plot-use/plot_use.py',['--quse',out/'regional-quse.csv','--out',out/'regional-use.png','--title','Regional pickup USE: 10 ms state samples'],log)
        tool('plot-sys-metrics/plot_sys_metrics.py',['--sysmetrics',out/'sysmetrics.csv','--out',out/'system.png','--title','System metrics: 1000 ms bins; latency in ms'],log)
        tool('plot-derive-event-metrics/plot_derive_event_metrics.py',[out/'emetrics.csv',out/'visit-hist.png','--hist','turnaround-time','--bins','30','--title','Per-queue visit duration (ms)'],log)
        tool('plot-derive-event-metrics/plot_derive_event_metrics.py',[out/'emetrics.csv',out/'visit-scatter.png','--scat','arrival-time,turnaround-time','--title','Per-queue duration by arrival (ms)'],log)
        tool('filter-by-job/filter_by_job.py',['--events',a/'events.log','--jobs',','.join(longest),'--out',out/'longest-jobs.log'],log)
        tool('pretty-print-job-trace/pretty_print_events.py',[out/'longest-jobs.log','--out',out/'longest-jobs-pretty.log','--no-color'],log)
        tool('create-event-trace/create_event_trace.py',['--events',out/'longest-jobs.log','--out',out/'longest-event-trace.png','-v'],log)


def plot_set(scenario,root,data):
    row,queue_rows,regional,events,states,orders=data
    out=RESULTS/scenario['name'];tools=out/'tools';tools.mkdir(parents=True,exist_ok=True)
    edges,matrices,queue_bins,system,uniform,completed=bins_and_samples(data)
    write_csv(out/'queue-timeseries.csv',queue_bins);write_csv(out/'system-timeseries.csv',system);write_csv(tools/'qmet-10ms.csv',uniform)
    longest=orders[orders.status=='delivered'].nlargest(3,'end_to_end_seconds')
    longest.to_csv(out/'longest-jobs.csv',index=False)
    accessory(root,tools,list(longest.order_id))
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    title=f"{scenario['name']} | 20 queues, {sum(scenario['couriers'])} couriers | seed 1"
    labels=[f'q{i} · restaurant {i} (R{i//3})' for i in range(15)]+[f'q{15+i} · region {i} pickup' for i in range(5)]
    def save(fig,name):
        fig.tight_layout();fig.savefig(out/name,dpi=140);plt.close(fig)
    for metric,name,label,cmap,vmax in [('utilization','utilization.png','Busy worker fraction','YlGnBu',1),
                                      ('waiting','saturation.png','Mean waiting orders','YlOrBr',70),
                                      ('drop_rate','errors.png','Queue-full drops / second','Reds',80)]:
        fig,ax=plt.subplots(figsize=(12,8))
        im=ax.pcolormesh(edges,np.arange(21),matrices[metric],cmap=cmap,vmin=0,vmax=max(vmax,float(matrices[metric].max())),shading='flat')
        ax.set_yticks(np.arange(20)+.5,labels);ax.invert_yaxis();ax.axhline(15,color='#333333',lw=1.5)
        ax.set_xlabel('Seconds from first event');ax.set_title(title+'\n'+label+' · 1-second bins',loc='left',fontweight='bold')
        fig.colorbar(im,ax=ax,label=label,pad=.02);save(fig,name)
    frame=pd.DataFrame(system);x=(frame.start_seconds+frame.end_seconds)/2
    fig,axes=plt.subplots(2,1,figsize=(11,7),sharex=True)
    axes[0].stairs(frame.throughput_per_second,edges,color='#176A8A',lw=2,label='Delivered')
    axes[0].axhline(row['offered_rate_per_second'],ls='--',color='#777777',label='Measured mean offered')
    axes[0].set_ylabel('Orders/s');axes[0].legend(frameon=False);axes[0].set_title(title+'\nDelivered throughput',loc='left',fontweight='bold')
    for region,color in enumerate(COLORS):axes[1].stairs(frame[f'region{region}_throughput'],edges,color=color,label=f'Region {region}')
    axes[1].set_ylabel('Regional orders/s');axes[1].set_xlabel('Seconds from first event');axes[1].legend(ncol=5,frameon=False)
    for ax in axes:ax.set_ylim(bottom=0);ax.grid(alpha=.15)
    save(fig,'throughput.png')
    fig,axes=plt.subplots(2,1,figsize=(11,7),sharex=True)
    axes[0].fill_between(x,frame.p10_latency_seconds,frame.p90_latency_seconds,color='#176A8A',alpha=.2,label='P10–P90')
    axes[0].plot(x,frame.median_latency_seconds,color='#176A8A',label='Median')
    axes[0].set_title(title+'\nDelivered-order latency by completion bin',loc='left',fontweight='bold');axes[0].legend(frameon=False)
    for region,color in enumerate(COLORS):axes[1].plot(x,frame[f'region{region}_median_latency'],color=color,label=f'Region {region}')
    axes[1].legend(ncol=5,frameon=False);axes[1].set_xlabel('Seconds from first event')
    for ax in axes:ax.set_ylabel('Latency (seconds)');ax.set_ylim(bottom=0);ax.grid(alpha=.15)
    save(fig,'latency.png')
    fig,ax=plt.subplots(figsize=(12,5.2));breakdown=[];bounds=[];ticklabels=[]
    for index,(_,order) in enumerate(longest.iterrows()):
        ev=events[events.job==order.order_id];end=float(completed[completed.job==order.order_id].time.iloc[0]);start=end-order.end_to_end_seconds
        bounds.extend([start,end]);ax.plot([start,end],[index,index],color='#999999',lw=2)
        r={'order_id':order.order_id,'region':int(order.region),'restaurant':int(order.restaurant),'end_to_end_seconds':order.end_to_end_seconds}
        for qid,color,phase in [(int(order.restaurant),'#176A8A','kitchen'),(15+int(order.region),'#C66A19','pickup')]:
            stamps=ev[ev.qid==qid].set_index('kind').time
            arrive=float(stamps['ARRIVE-VIA']);enter=float(stamps['ENTERS-SERVER']);exit=float(stamps['EXITS-SERVER'])
            ax.barh(index,enter-arrive,left=arrive,height=.35,color=color,alpha=.25)
            ax.barh(index,exit-enter,left=enter,height=.35,color=color)
            r[phase+'_wait_seconds']=enter-arrive;r[phase+'_service_seconds']=exit-enter
        r['transfer_output_seconds']=r['end_to_end_seconds']-sum(r[k] for k in r if k.endswith('_wait_seconds') or k.endswith('_service_seconds'))
        breakdown.append(r);ax.scatter([start,end],[index,index],color='#253746',s=20,zorder=3)
        ticklabels.append(f'{order.order_id}\nR{int(order.region)} · {order.end_to_end_seconds:.3f} s')
    ax.set_yticks(range(3),ticklabels);ax.invert_yaxis();span=max(bounds)-min(bounds);ax.set_xlim(min(bounds)-.03*span,max(bounds)+.03*span)
    ax.set_title(title+'\nThree longest delivered orders',loc='left',fontweight='bold');ax.set_xlabel('Seconds from first event');ax.grid(axis='x',alpha=.15)
    ax.legend(handles=[Patch(color='#176A8A',label='Kitchen service'),Patch(color='#C66A19',label='Delivery service'),Patch(color='#888888',alpha=.25,label='Pale bars: queue wait')],loc='upper center',bbox_to_anchor=(.5,-.2),ncol=3,frameon=False)
    save(fig,'longest-jobs.png');write_csv(out/'longest-job-breakdown.csv',breakdown)


def report():
    RESULTS.mkdir(exist_ok=True)
    rows=[];queues=[];regions=[]
    for scenario in SPEC['scenarios']:
        for seed in SPEC['seeds']:
            with saved_run(scenario['name'],seed) as root:
                data=measure(root,scenario,seed);rows.append(data[0]);queues.extend(data[1]);regions.extend(data[2])
                if seed==SPEC['representative_seed']:plot_set(scenario,root,data)
        print('PLOTS '+scenario['name'],flush=True)
    frame=pd.DataFrame(rows)
    if any(g.workload_sha256.nunique()!=1 for _,g in frame.groupby('seed')):raise RuntimeError('paired workload mismatch')
    frame.to_csv(RESULTS/'metrics-by-run.csv',index=False);pd.DataFrame(queues).to_csv(RESULTS/'metrics-by-queue.csv',index=False);pd.DataFrame(regions).to_csv(RESULTS/'metrics-by-region.csv',index=False)
    numeric=[c for c in frame.columns if c not in ('scenario','seed','workload_sha256')]
    aggregate=frame.groupby('scenario')[numeric].agg(['mean','min','max']);aggregate.columns=['_'.join(c) for c in aggregate.columns];aggregate.to_csv(RESULTS/'metrics-by-scenario.csv')
    comparison=[]
    for scenario in SPEC['scenarios']:
        g=frame[frame.scenario==scenario['name']].set_index('seed');base=frame[frame.scenario=='baseline'].set_index('seed')
        comparison.append({'scenario':scenario['name'],'couriers':sum(scenario['couriers']),'regional_couriers':','.join(map(str,scenario['couriers'])),
            'courier_change_percent':scenario['change_percent'],'total_worker_change_percent':100*((30+sum(scenario['couriers']))/50-1),
            'mean_throughput':g.throughput_orders_per_second.mean(),'paired_throughput_change_percent':((g.throughput_orders_per_second/base.throughput_orders_per_second-1)*100).mean(),
            'mean_latency_seconds':g.mean_latency_seconds.mean(),'paired_latency_change_percent':((g.mean_latency_seconds/base.mean_latency_seconds-1)*100).mean(),
            'mean_p95_latency_seconds':g.p95_latency_seconds.mean(),'mean_loss_percent':g.loss_percent.mean(),
            'mean_kitchen_drops':g.kitchen_drops.mean(),'mean_pickup_drops':g.pickup_drops.mean(),'mean_offered_rate':g.offered_rate_per_second.mean()})
    write_csv(RESULTS/'comparison.csv',comparison)
    fig,axes=plt.subplots(1,3,figsize=(14,4.6))
    for ax,column,label in zip(axes,['throughput_orders_per_second','mean_latency_seconds','loss_percent'],['Delivered orders/s','Mean delivered latency (s)','Order loss (%)']):
        g=frame.groupby('couriers')[column].agg(['mean','min','max']).sort_index()
        ax.errorbar(g.index,g['mean'],yerr=[g['mean']-g['min'],g['max']-g['mean']],fmt='o-',capsize=4,color='#176A8A')
        ax.axvline(20,ls='--',color='#999999');ax.set_xlabel('Total regional couriers');ax.set_ylabel(label);ax.set_ylim(bottom=0);ax.grid(alpha=.15)
    fig.suptitle('20 queues · mean and min–max across three paired seeds',fontweight='bold');fig.tight_layout();fig.savefig(RESULTS/'sensitivity-comparison.png',dpi=150);plt.close(fig)
    manifest=[{'scenario':s['name'],'plot':p,'path':f"{s['name']}/{p}"} for s in SPEC['scenarios'] for p in PLOTS]
    write_csv(RESULTS/'plot-manifest.csv',manifest)
    for item in manifest:
        if not (RESULTS/item['path']).is_file():raise RuntimeError('missing main plot')
    (RESULTS/'validation.json').write_text(json.dumps({'status':'passed','queues':20,'runs':len(rows),'main_plots':len(manifest),
        'generated':int(frame.generated.sum()),'delivered':int(frame.delivered.sum()),'dropped':int(frame.dropped.sum()),
        'incomplete':0,'paired_workloads_match':True,'regional_routing_checked':True,'all_registries_drained':True,
        'all_accessory_commands_succeeded':True},indent=2)+'\n')
    print(pd.DataFrame(comparison).to_string(index=False),flush=True)


if __name__=='__main__':report()
