import csv
import json
from pathlib import Path
from job import Job, task_parts

def load_workloads(config_path, configs):
    root = Path(config_path).resolve().parent
    payloads = {}

    for line in (root/'jobs.jsonl').read_text().splitlines():
        p = json.loads(line)
        if p['job_id'] in payloads:
            raise ValueError('duplicate payload ID')

        payloads[p['job_id']] = p

    sources, seen = [], set()

    for cfg in configs:
        if not cfg['Source']:
            continue

        with (root/cfg['Source']).open(newline='') as f:
            for row in csv.DictReader(f):
                jid = row['Job Id']

                if jid in seen or jid not in payloads:
                    raise ValueError('duplicate or missing payload for ' + jid)

                seen.add(jid)
                tasks = json.loads(row['Tasks'])

                if not tasks:
                    raise ValueError('empty input task list')

                for task in tasks:
                    task_parts(task)

                arrival = int(row['arrival time'])

                if arrival < 0:
                    raise ValueError('arrival must be nonnegative')

                sources.append((arrival, cfg['Sys ID'], Job(jid, arrival, tasks, payloads[jid])))
    
    if seen != set(payloads):
        raise ValueError('workload CSV and jobs.jsonl coverage differs')

    return root, sorted(sources, key=lambda x: (x[0], int(x[2].id[1:])))

def write_sink(path, jobs, clock):
    with Path(path).open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Job Id','arrival time','Tasks'])
        
        for jid, tick in jobs:
            writer.writerow([jid,tick,'[]'])
