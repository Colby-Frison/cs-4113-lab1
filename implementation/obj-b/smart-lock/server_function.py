"""Application functions. No queue scheduling, event writing, or plotting here."""
import hashlib
from pathlib import Path
import time
from scanner import clean_source, find_candidates

def read_source(job,root):
    path=(Path(root)/job.payload['source']).resolve()
    if not path.is_relative_to(Path(root).resolve()/'corpus'):raise ValueError('source escapes corpus')
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=job.payload['sha256']:raise ValueError('source hash mismatch')
    return data.decode('utf-8',errors='replace')

def prepare(job,root):
    job.prepared=clean_source(read_source(job,root));return job

def scan(job):
    job.findings=find_candidates(job.prepared,job.payload['check']);job.prepared='';return job

def review(job):
    job.status='candidate' if job.findings else 'no_candidate';return job

def report(job):return job

def monolithic(job,root):
    # Original combined implementation, retained alongside decomposed functions.
    code=clean_source(read_source(job,root))
    job.findings=find_candidates(code,job.payload['check'])
    job.status='candidate' if job.findings else 'no_candidate'
    return job

def execute(stage,job,root,engine='python',endpoint=None,rpc_timeout=30):
    if engine=='cpp-grpc':
        from rpc_client import execute_rpc
        return execute_rpc(stage,job,root,endpoint,rpc_timeout)
    if stage=='Monolithic':return monolithic(job,root)
    if stage=='Prepare':return prepare(job,root)
    if stage=='Scan':return scan(job)
    if stage=='Review':return review(job)
    if stage=='Report':return report(job)
    raise ValueError('unknown application function '+stage)
