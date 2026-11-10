"""Quantify observed source gaps without treating requested arrivals as exact replay."""
import csv
from run_study import HERE, RESULTS, RUNS, SPEC
from report_study import saved_run, write_csv


def diagnose():
    rows=[]
    for scenario in SPEC['scenarios']:
        for seed in SPEC['seeds']:
            with saved_run(scenario['name'],seed) as root:
                with (root/'jobs.csv').open() as f:jobs=list(csv.reader(f))[1:]
            gaps=[float(b[1])-float(a[1]) for a,b in zip(jobs,jobs[1:])]
            rows.append({'scenario':scenario['name'],'seed':seed,'max_gap_seconds':max(gaps),
                         'gaps_over_100ms':sum(g>.1 for g in gaps),'gaps_over_1s':sum(g>1 for g in gaps)})
    write_csv(RESULTS/'arrival-diagnostics.csv',rows)


if __name__=='__main__':diagnose()
