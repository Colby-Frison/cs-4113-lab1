"""Write qmet schema from measured queue transitions."""
import csv
import math
import json
from pathlib import Path

class MetricsWriter:
    def __init__(self,path):self.path=Path(path)

    def write(self,log,configs,max_rows=200000,sample_ticks=10):
        ids=[c['Sys ID'] for c in configs]

        # cap aka num servers 
        capacity={c['Sys ID']:c['Num. Servers'] for c in configs}

        end=log.clock()+1

        # max of sample ticks and ceil of (end+1)*len(ids)/max_rows, waiting and listing all ticks from 0 to end+stride with step stride
        stride=max(sample_ticks,math.ceil((end+1)*len(ids)/max_rows))
        waiting={q:0 for q in ids};busy=waiting.copy();index=0
        ticks=list(range(0,end+stride,stride))

        # formatting really, im too lazy to understand the og formatting so just doing my own wya
        with self.path.open('w',newline='') as f:
            writer=csv.writer(f)
            writer.writerow(['time-step','queue-sys-id','num-jobs-in-queue','num-jobs-in-service','num-of-errors'])

            # loops through ticks, and for each loops through transitions until the transition time is greater than the tick time, updating waiting, busy, and errors for each queue
            for t in ticks:
                errors={q:0 for q in ids}

                # loops through transitions until the transition time is greater than the tick time, updating waiting, busy, and errors for each queue
                while index<len(log.transitions) and log.transitions[index][0]<=t:
                    _,q,w,b,e=log.transitions[index];waiting[q]+=w;busy[q]+=b;errors[q]+=e;index+=1
                
                for q in ids:
                    if waiting[q]<0 or not 0<=busy[q]<=capacity[q]:raise ValueError('invalid queue counters')
                    writer.writerow([t,int(q[1:]),waiting[q],busy[q],errors[q]])

        # stuff
        info={'tick_microseconds':log.tick_ns/1000,
              'qmet_sample_seconds':stride*log.tick_ns/1e9
        }

        self.path.with_name('metrics-info.json').write_text(json.dumps(info,indent=2)+'\n')
        return info
