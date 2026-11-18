#!/usr/bin/env python3
"""Run original Obj A tools on all completed experiment sets."""
import argparse
import csv
from pathlib import Path
from analyze_all import analyze

def main():

    # arguments
    app=Path(__file__).resolve().parents[1]
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--suite',type=Path,required=True)
    p.add_argument('--tools-root',type=Path,default=app.parent.parent/'obj-a')
    p.add_argument('--skip-existing',action='store_true')
    a=p.parse_args()

    # loops through sum and making the plots
    for summary in sorted(a.suite.glob('*/*/outputs/summary.json')):
        if a.skip_existing and (summary.parent/'longest-trace.png').exists():continue
        analyze(summary.parent,a.tools_root)

    if (a.suite/'suite_summary.csv').exists():
        with (a.suite/'suite_summary.csv').open(newline='') as f:rows=list(csv.DictReader(f))

        # Veras happy moment, he gets pictures of plots
        for row in rows:row['plots_generated']=str((a.suite/row['scale']/row['profile']/'outputs/longest-trace.png').is_file())
        with (a.suite/'suite_summary.csv').open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)

if __name__=='__main__':main()
