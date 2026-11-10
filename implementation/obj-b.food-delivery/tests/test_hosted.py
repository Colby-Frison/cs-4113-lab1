"""Hosted isolation, deadline handling, and end-to-end accounting."""
import asyncio
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

APP=Path(__file__).resolve().parents[1];sys.path.insert(0,str(APP))
from run_hosted import run,workload
from test_network import config
from fd import APP
from lib.job import format_job_line,Task
try:
    import grpc
    from lib.proto_gen import job_pb2,queue_to_server_pb2_grpc
except ImportError:grpc=None
BINARY=APP/'cpp/build/food_host'


@unittest.skipUnless(grpc and BINARY.exists(),'requires built food_host and gRPC')
class HostedRunTests(unittest.TestCase):
    def test_shared_network_routes_and_drains(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=run(tmp,config(),timeout=30)
            self.assertEqual(result['delivered'],60)
            self.assertEqual(result['dropped'],0)
            self.assertEqual(result['server_processes'],2)
            self.assertEqual(result['incomplete'],0)
            runtime=json.loads((Path(tmp)/'runtime.json').read_text())
            self.assertEqual(runtime['logical_queues'],6)
            for pid in runtime['hosts'].values():
                with self.assertRaises(ProcessLookupError):os.kill(pid,0)

    def test_burst_and_unvisited_queues_remain_accounted(self):
        c={**config(),'count':1,'arrival_mean':0}
        with tempfile.TemporaryDirectory() as tmp:
            result=run(tmp,c,timeout=30)
            self.assertEqual(result['delivered'],1)
            with (Path(tmp)/'analysis/qmet.csv').open() as f:
                self.assertEqual({int(r['queue-sys-id']) for r in csv.DictReader(f)},set(range(6)))
        c={**config(),'count':500,'arrival_mean':0,'pickup_capacity':1,'kitchen_capacity':1,'couriers':[1,1]}
        with tempfile.TemporaryDirectory() as tmp:
            result=run(tmp,c,timeout=30)
            self.assertGreater(result['dropped'],0)
            self.assertEqual(result['delivered']+result['dropped'],500)

    def test_deadline_is_failed_run_not_success(self):
        c={**config(),'count':10,'arrival_mean':5,'time_scale':1}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Exception):run(tmp,c,timeout=.5)
            root=Path(tmp)
            self.assertEqual(json.loads((root/'status.json').read_text())['status'],'failed')
            self.assertFalse((root/'analysis/summary.json').exists())

    def test_two_hundred_queues(self):
        c={**config(),'restaurant_regions':[r for r in range(50) for _ in range(3)],
            'restaurant_weights':[1]*150,'kitchen_workers':[2]*150,'couriers':[4]*50,
            'count':3000,'arrival_mean':.002,'time_scale':.1}
        with tempfile.TemporaryDirectory() as tmp:
            result=run(tmp,c,timeout=60)
            self.assertEqual(result['queues'],200)
            self.assertEqual(result['generated'],result['delivered']+result['dropped'])
            self.assertEqual(result['server_processes'],2)
            with (Path(tmp)/'analysis/qmet.csv').open() as f:
                self.assertEqual({int(r['queue-sys-id']) for r in csv.DictReader(f)},set(range(200)))


@unittest.skipUnless(grpc and BINARY.exists(),'requires built food_host and gRPC')
class HostProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        entries=[{'qid':f'q{i}','stage':'courier','workers':1,'restaurants':[i],
            'logfile':str(self.root/f'q{i}.log'),'destination':str(self.root/f'sink-{i}.csv')} for i in (0,1)]
        (self.root/'host.json').write_text(json.dumps({'queues':entries}))
        self.err=(self.root/'stderr').open('w')
        self.process=subprocess.Popen([str(BINARY),'--config',str(self.root/'host.json'),'--ready-file',str(self.root/'ready')],stderr=self.err)
        deadline=time.monotonic()+10
        while not (self.root/'ready').exists():
            if self.process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('host startup failed')
            time.sleep(.01)
        self.channel=grpc.insecure_channel('127.0.0.1:'+ (self.root/'ready').read_text().strip())
        grpc.channel_ready_future(self.channel).result(timeout=5)
        self.stub=queue_to_server_pb2_grpc.QueueToServerStub(self.channel)

    def tearDown(self):
        self.channel.close();self.process.terminate();self.process.wait(timeout=5);self.err.close();self.temp.cleanup()

    def request(self,seq=0,restaurant=0,qid=0,wait=.001):
        row=format_job_line(f'o{seq}.r{restaurant}.near.i1',time.time(),[Task(0,.001),Task(1,wait)],wait_precision=3)
        return job_pb2.SubmitJobRequest(source_edge=f'q{qid}',job=job_pb2.Job(raw_line=row))

    def submit(self,request,worker='0',timeout=2):
        return self.stub.Submit(request,metadata=(('worker-id',worker),),timeout=timeout)

    def test_routes_and_worker_limits_are_enforced(self):
        for req,worker in [(self.request(restaurant=1),'0'),(self.request(qid=99),'0'),(self.request(),'1')]:
            with self.assertRaises(grpc.RpcError) as caught:self.submit(req,worker)
            self.assertEqual(caught.exception.code(),grpc.StatusCode.INVALID_ARGUMENT)
        pending=self.stub.Submit.future(self.request(wait=.3),metadata=(('worker-id','0'),),timeout=2)
        deadline=time.monotonic()+1
        while 'ENTERS-SERVER' not in (self.root/'q0.log').read_text():
            self.assertLess(time.monotonic(),deadline);time.sleep(.002)
        with self.assertRaises(grpc.RpcError) as caught:self.submit(self.request(seq=1))
        self.assertEqual(caught.exception.code(),grpc.StatusCode.RESOURCE_EXHAUSTED)
        self.assertTrue(self.submit(self.request(restaurant=1,qid=1)).accepted)
        self.assertFalse(pending.done(),'one queue blocked the independent queue')
        self.assertTrue(pending.result().accepted)
        with self.assertRaises(grpc.RpcError) as caught:self.submit(self.request())
        self.assertEqual(caught.exception.code(),grpc.StatusCode.ALREADY_EXISTS)

    def test_expired_service_does_not_reach_sink(self):
        with self.assertRaises(grpc.RpcError):self.submit(self.request(wait=.3),timeout=.02)
        time.sleep(.05)
        self.assertEqual((self.root/'sink-0.csv').read_text(),'')
        self.assertTrue(self.submit(self.request(seq=1)).accepted)


class ArrivalPlanTests(unittest.TestCase):
    def test_resource_changes_preserve_absolute_plan(self):
        original=config();changed={**original,'couriers':[3,4],'kitchen_workers':[2]*4}
        self.assertEqual(workload(original),workload(changed))
        plan=workload(original)
        self.assertTrue(all(a[0]<b[0] for a,b in zip(plan,plan[1:])))


if __name__=='__main__':unittest.main()
