"""logging belongs to the queue runtime, mainly just separated out to keep the main cleaner"""
import time
from pathlib import Path

class EventLog:
    def __init__(self,path,tick_us):
        self.path=Path(path);self.origin=time.perf_counter_ns();self.tick_ns=tick_us*1000
        self.records=[];self.transitions=[]

    def clock(self):
        return (time.perf_counter_ns()-self.origin)//self.tick_ns

    def log(self,jid,qid,event,waiting=0,busy=0,errors=0):
        tick=self.clock()
        self.records.append((tick,jid,qid,event))

        # only log transitions if there is a change in waiting, busy, or errors
        if waiting or busy or errors:self.transitions.append((tick,qid,waiting,busy,errors))
        return tick

    def flush(self):
        self.path.write_text(''.join(f'{t} {j} IN {q} {e}\n' for t,j,q,e in self.records))
        starts={};durations={}

        for t,j,q,e in self.records:
            if e.startswith('ARRIVE-VIA') and j not in starts:starts[j]=t
            if e.startswith('DEPART-VIA out.wl'):durations[j]=t-starts[j]

        selected=set(sorted(durations,key=durations.get,reverse=True)[:10])
        self.path.with_name('events-longest.log').write_text(''.join(f'{t} {j} IN {q} {e}\n' for t,j,q,e in self.records if j in selected))
