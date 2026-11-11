"""Shared C++ host routing, worker isolation, and cancellation checks."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

APP=Path(__file__).resolve().parents[1];sys.path.insert(0,str(APP))
from fd import APP
from lib.job import format_job_line,Task
try:
    import grpc
    from lib.proto_gen import job_pb2,queue_to_server_pb2_grpc
except ImportError:grpc=None
BINARY=APP/'cpp/build/food_host'


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


if __name__ == '__main__':
    unittest.main()
