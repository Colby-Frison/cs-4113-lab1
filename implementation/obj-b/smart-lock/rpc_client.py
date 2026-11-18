"""Client for the actual C++ server functions"""
import sys
from pathlib import Path
import threading
from server_function import read_source

_local=threading.local()

def execute_rpc(stage,job,root,endpoint,timeout):
    import grpc # why is it always bugggyyyy bruh

    sys.path.insert(0,str(Path(__file__).resolve().parent/'proto'))
    import smart_lock_pb2 as pb # from proto that I was working on

    # caches and calls C++, had some endpoint issues at first, but now it works, yay
    if not hasattr(_local,'channel') or _local.endpoint!=endpoint:
        _local.channel=grpc.insecure_channel(endpoint,options=[('grpc.max_receive_message_length',32*1024*1024),('grpc.max_send_message_length',32*1024*1024)])
        _local.endpoint=endpoint
        _local.call=_local.channel.unary_unary('/smartlock.Scanner/Execute',request_serializer=pb.Request.SerializeToString,response_deserializer=pb.Reply.FromString)

    source=read_source(job,root) if stage in ('Prepare','Monolithic') else job.prepared if stage=='Scan' else ''

    req=pb.Request(stage=stage,source=source,check=job.payload['check'],status=job.status)
    
    for finding in job.findings:req.findings.add(**finding)
    
    reply=_local.call(req,timeout=timeout)
    
    if stage=='Prepare':job.prepared=reply.prepared
    
    if stage in ('Scan','Monolithic','Review','Report'):
        job.findings=[dict(rule=f.rule,line=f.line,symbol=f.symbol,reason=f.reason) for f in reply.findings]
    
    if stage in ('Scan','Monolithic'):job.prepared=''
    
    if reply.status:job.status=reply.status
    return job
