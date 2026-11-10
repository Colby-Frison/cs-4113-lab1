"""Regional routing, worker isolation, and multi-queue accounting checks."""
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
from fd.workload import generate
from fd.order import validate_order
from lib.job import iter_jobs
from run_network import validate_config, run
try:
    import grpc
except ImportError:
    grpc=None


def config():
    return {'restaurant_regions':[0,0,1,1], 'restaurant_weights':[.4,.1,.3,.2],
            'kitchen_workers':[1]*4,'couriers':[2,2],'kitchen_capacity':32,'pickup_capacity':32,
            'count':60,'arrival_mean':.02,'time_scale':.2,'seed':4}


class NetworkTests(unittest.TestCase):
    def test_bad_mapping_rejected(self):
        c=config();c['restaurant_regions']=[0,0,2,2]
        with self.assertRaises(ValueError):validate_config(c)

    def test_restaurant_routing_does_not_change_service_rng(self):
        single,multi=io.StringIO(),io.StringIO()
        for out,weights in [(single,None),(multi,config()['restaurant_weights'])]:
            generate(out,count=60,seed=4,sleep=lambda _:None,clock=lambda:10,restaurant_weights=weights)
        a=list(iter_jobs(single.getvalue().splitlines()));b=list(iter_jobs(multi.getvalue().splitlines()))
        self.assertEqual([j.tasks for j in a],[j.tasks for j in b])
        self.assertEqual({validate_order(j).restaurant for j in b},{0,1,2,3})

    @unittest.skipUnless(grpc and (APP/'cpp/build/food_server').exists(),'requires C++ build and gRPC')
    def test_shared_regions_route_and_drain(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=run(tmp,config(),timeout=20)
            self.assertEqual(result['queues'],6)
            self.assertEqual(result['generated'],60)
            self.assertEqual(result['delivered'],60)
            self.assertEqual(result['dropped'],0)
            self.assertEqual(result['incomplete'],0)
            root=Path(tmp)
            events=(root/'analysis/events.log').read_text()
            for i in range(6):self.assertIn(f' IN q{i} ',events)
            for region in (0,1):
                for sink in (root/f'region-{region}').glob('sink-*.csv'):
                    for job in iter_jobs(sink.read_text().splitlines()):
                        self.assertEqual(config()['restaurant_regions'][validate_order(job).restaurant],region)

    @unittest.skipUnless(grpc and (APP/'cpp/build/food_server').exists(),'requires C++ build and gRPC')
    def test_overload_is_accounted(self):
        c={**config(),'count':150,'arrival_mean':0,'pickup_capacity':1,'kitchen_capacity':2,'couriers':[1,1]}
        with tempfile.TemporaryDirectory() as tmp:
            result=run(tmp,c,timeout=20)
            self.assertGreater(result['dropped'],0)
            self.assertEqual(result['generated'],result['delivered']+result['dropped'])
            self.assertEqual(result['incomplete'],0)


if __name__=='__main__':unittest.main()
