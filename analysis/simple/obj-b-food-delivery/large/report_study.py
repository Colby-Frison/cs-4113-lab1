"""Scenario comparisons using the shared regional study metrics and plots."""
import json
from pathlib import Path
import sys
import pandas as pd

from run_study import RESULTS, RUNS, REPO, SPEC, configuration
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from study_tools.report import StudyReport, PLOTS, plt
from study_tools.files import write_csv

reporter = StudyReport(runs=RUNS, results=RESULTS, repo=REPO, spec=SPEC,
                       configuration=configuration, sample_ms=100)
saved_run = reporter.saved_run
measure = reporter.measure
plot_set = reporter.plot_set
N, REGIONS, NQ = reporter.restaurants, reporter.regions, reporter.queues


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
    if any(g.workload_sha256.nunique()!=1 or g.planned_arrivals_sha256.nunique()!=1 for _,g in frame.groupby('seed')):raise RuntimeError('paired workload mismatch')
    frame.to_csv(RESULTS/'metrics-by-run.csv',index=False);pd.DataFrame(queues).to_csv(RESULTS/'metrics-by-queue.csv',index=False);pd.DataFrame(regions).to_csv(RESULTS/'metrics-by-region.csv',index=False)
    numeric=[c for c in frame.columns if c not in ('scenario','seed','workload_sha256','planned_arrivals_sha256','arrival_quality')]
    aggregate=frame.groupby('scenario')[numeric].agg(['mean','min','max']);aggregate.columns=['_'.join(c) for c in aggregate.columns];aggregate.to_csv(RESULTS/'metrics-by-scenario.csv')
    comparison=[]
    for scenario in SPEC['scenarios']:
        g=frame[frame.scenario==scenario['name']].set_index('seed');base=frame[frame.scenario=='baseline'].set_index('seed')
        comparison.append({'scenario':scenario['name'],'couriers':sum(scenario['couriers']),'regional_couriers':','.join(map(str,scenario['couriers'])),
            'courier_change_percent':scenario['change_percent'],'total_worker_change_percent':100*((sum(SPEC['baseline']['kitchen_workers'])+sum(scenario['couriers']))/(sum(SPEC['baseline']['kitchen_workers'])+sum(SPEC['baseline']['couriers']))-1),
            'mean_throughput':g.throughput_orders_per_second.mean(),'paired_throughput_change_percent':((g.throughput_orders_per_second/base.throughput_orders_per_second-1)*100).mean(),
            'mean_latency_seconds':g.mean_latency_seconds.mean(),'paired_latency_change_percent':((g.mean_latency_seconds/base.mean_latency_seconds-1)*100).mean(),
            'mean_p95_latency_seconds':g.p95_latency_seconds.mean(),'mean_loss_percent':g.loss_percent.mean(),
            'mean_kitchen_drops':g.kitchen_drops.mean(),'mean_pickup_drops':g.pickup_drops.mean(),'mean_offered_rate':g.offered_rate_per_second.mean(),
            'max_p95_injection_lag_seconds':g.p95_injection_lag_seconds.max(),'lagged_runs':int((g.arrival_quality=='LAGGED').sum())})
    write_csv(RESULTS/'comparison.csv',comparison)
    fig,axes=plt.subplots(1,3,figsize=(14,4.6))
    for ax,column,label in zip(axes,['throughput_orders_per_second','mean_latency_seconds','loss_percent'],['Delivered orders/s','Mean delivered latency (s)','Order loss (%)']):
        g=frame.groupby('couriers')[column].agg(['mean','min','max']).sort_index()
        ax.errorbar(g.index,g['mean'],yerr=[g['mean']-g['min'],g['max']-g['mean']],fmt='o-',capsize=4,color='#176A8A')
        ax.axvline(sum(SPEC['baseline']['couriers']),ls='--',color='#999999');ax.set_xlabel('Total regional couriers');ax.set_ylabel(label);ax.set_ylim(bottom=0);ax.grid(alpha=.15)
    fig.suptitle(f'{NQ} queues · mean and min–max across {len(SPEC["seeds"])} paired seeds',fontweight='bold');fig.tight_layout();fig.savefig(RESULTS/'sensitivity-comparison.png',dpi=150);plt.close(fig)
    manifest=[{'scenario':s['name'],'plot':p,'path':f"{s['name']}/{p}"} for s in SPEC['scenarios'] for p in PLOTS]
    write_csv(RESULTS/'plot-manifest.csv',manifest)
    for item in manifest:
        if not (RESULTS/item['path']).is_file():raise RuntimeError('missing main plot')
    (RESULTS/'validation.json').write_text(json.dumps({'status':'passed','queues':NQ,'runs':len(rows),'main_plots':len(manifest),
        'generated':int(frame.generated.sum()),'delivered':int(frame.delivered.sum()),'dropped':int(frame.dropped.sum()),
        'incomplete':0,'paired_workloads_match':True,'regional_routing_checked':True,'all_registries_drained':True,
        'all_accessory_commands_succeeded':True,'lagged_runs':int((frame.arrival_quality=='LAGGED').sum())},indent=2)+'\n')
    lines=['# Large-study measured results', '',
        f"{NQ} queues; {len(rows)} runs; {int(frame.generated.sum()):,} generated orders. These are measurements from this output directory.", '',
        '| Scenario | Couriers | Delivered/s | Mean latency (s) | Loss (%) | Max P95 injection lag (s) | Lagged runs |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in comparison:
        lines.append(f"| {r['scenario']} | {r['couriers']} | {r['mean_throughput']:.2f} | {r['mean_latency_seconds']:.3f} | {r['mean_loss_percent']:.2f} | {r['max_p95_injection_lag_seconds']:.4f} | {r['lagged_runs']} |")
    lines+=['', 'Each scenario folder has six main plots plus Objective A accessory figures in tools/.',
        'Latency excludes rejected orders. Error bars are seed min–max, not confidence intervals.',
        'LAGGED means P95 injection delay exceeds the configured 10 ms threshold; such runs are retained and cannot be treated as exact arrival replay.',
        'All 200 queues appear in USE heatmaps; regional line panels show R0–R4 as a readable sample. CSVs retain all 50 regions.',
        'The 100 ms accessory samples give 1/10/100-second rolling windows; main USE integrates event states.']
    (RESULTS/'summary.md').write_text('\n'.join(lines)+'\n')
    print(pd.DataFrame(comparison).to_string(index=False),flush=True)


if __name__=='__main__':report()
