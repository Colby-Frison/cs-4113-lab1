"""One real FCFS waiting queue and its reserved server slots, like Colby's legacy stuff."""
from collections import deque

class QueueSystem:
    def __init__(self, cfg):
        self.config=cfg
        self.id=cfg['Sys ID']
        self.waiting=deque()
        self.busy={}
        self.cursor=0

    def admit(self, job):
        if len(self.waiting)>=self.config['Queue Max Size']:
            return False
        self.waiting.append(job)
        return True

    def idle_server(self):
        return next((i for i in range(self.config['Num. Servers']) if i not in self.busy), None)

    def destination(self):
        edges=self.config['edges']
        if not edges:return None
        destination=edges[self.cursor%len(edges)]
        self.cursor+=1
        return destination
