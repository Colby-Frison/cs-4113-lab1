#!/usr/bin/env python3
"""Run the existing implementation/obj-a tools"""
import argparse
import csv
import json
from pathlib import Path
import subprocess
import sys

def locate(root,name):
    direct=root/name.replace('_','-')/(name+'.py')
    if direct.is_file():return direct
    hits=list(root.rglob(name+'.py'))
    if len(hits)!=1:raise ValueError(f'Expected one {name}.py under {root}; found {len(hits)}. Use --tools-root.')
    return hits[0]

def analyze(results,tools_root,window=100):
    results=Path(results).resolve();tools_root=Path(tools_root).resolve()
    names=['compute_use','derive_sys_metrics','derive_event_metrics','plot_use','plot_sys_metrics',
           'plot_derive_event_metrics','create_event_trace','filter_by_queue','filter_by_job']
    
    tools={name:locate(tools_root,name) for name in names}
    
    for name in ['qmet.csv','events.log','qconf.csv','events-longest.log']:
        if not (results/name).is_file():raise ValueError('missing '+str(results/name))
    
    info=json.loads((results/'metrics-info.json').read_text());unit=f"1 tick = {info['tick_microseconds']/1000:g} ms"
    with (results/'qconf.csv').open() as f:configs=list(csv.DictReader(f))
    
    # Plot up to eight nodes, avoiding an unreadable 1000-color legend for now.
    indices=sorted(set([0,len(configs)-1]+[i for i,r in enumerate(configs) if r['Server Function'] in ('Review','Verify')]+[i for i,r in enumerate(configs) if r['Server Function']=='Scan'][:8]))
    qids=','.join(configs[i]['Sys ID'][1:] for i in indices)
    first=(results/'events-longest.log').read_text().splitlines()
    longest_jobs=list(dict.fromkeys(line.split()[1] for line in first))
    
    commands=[
      ('filter_by_queue',['--qmet','qmet.csv','--qid',qids,'--out','selected-qmet.csv']),
      ('filter_by_job',['--events','events.log','--jobs',','.join(longest_jobs) if longest_jobs else 'j0','--out','longest-filtered.log']),
      ('compute_use',['--qmet','qmet.csv','--config','qconf.csv','--out','quse.csv']),
      ('derive_sys_metrics',['--events','events.log','--out','sysmetrics.csv','--window',str(window),'--dense']),
      ('derive_event_metrics',['--events','events.log','--out','emetrics.csv']),
      ('plot_use',['--quse','quse.csv','--qid',qids,'--out','use.png','--title','Smart-lock queue USE; '+unit]),
      ('plot_sys_metrics',['--sysmetrics','sysmetrics.csv','--out','sysmetrics.png','--title','Smart-lock throughput / latency; '+unit]),
      ('plot_derive_event_metrics',['emetrics.csv','waiting-hist.png','--hist','time-in-queue','--title','Queue wait; '+unit]),
      ('plot_derive_event_metrics',['emetrics.csv','service-hist.png','--hist','time-in-service','--title','Service time; '+unit]),
      ('plot_derive_event_metrics',['emetrics.csv','arrival-latency.png','--scat','arrival-time,turnaround-time','--title','Queue visit turnaround; '+unit]),
      ('create_event_trace',['--events','longest-filtered.log','--out','longest-trace.png']),
    ]
    
    for name,flags in commands:
        print('Obj A:',name,flush=True)
        subprocess.run([sys.executable,str(tools[name]),*flags],cwd=results,check=True)
    (results/'plot-info.json').write_text(json.dumps(dict(tools_root=str(tools_root),selected_queues=qids,
        system_window_ticks=window,system_window_seconds=window*info['tick_microseconds']/1000000,
        note='USE windows are 10/100/1000 qmet samples; system throughput counts completions per window, not per second.'),indent=2)+'\n')
   
    print('Graphs saved in',results)

if __name__=='__main__':
    app=Path(__file__).resolve().parents[1]
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--results',type=Path,required=True)
    p.add_argument('--tools-root',type=Path,default=app.parent.parent/'obj-a')
    p.add_argument('--window',type=int,default=100)
    a=p.parse_args()
    try:
        if a.window<1:raise ValueError('window must be positive') # was bugging
        analyze(a.results,a.tools_root,a.window)
    except (ValueError,OSError,subprocess.CalledProcessError) as exc:p.error(str(exc))
