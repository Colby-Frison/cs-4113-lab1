import copy
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
from config_io import build_config
from job import Job
from scanner import clean_source,find_candidates
from server_function import prepare,scan,review,monolithic

class ScannerTests(unittest.TestCase):
    def test_comments_literals_and_line_numbers(self):
        code='// strcpy(a,b)\nconst char*s="unlockDoor()";\nvoid f(char*s){ strcpy(buffer,s); }\n'
        findings=find_candidates(clean_source(code),'input_bounds')
        self.assertEqual([(f['line'],f['symbol']) for f in findings],[(3,'strcpy')])
        self.assertEqual(find_candidates(clean_source(code),'authorization'),[])

    def test_auth_guard_and_freshness_are_review_heuristics(self):
        code='void handle(){if(isAllowed()) unlockDoor();} // nonce\n'
        cleaned=clean_source(code)
        self.assertEqual(find_candidates(cleaned,'authorization'),[])
        self.assertEqual(len(find_candidates(cleaned,'replay')),1)

    def test_combined_and_decomposed_match(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'corpus').mkdir()
            data=b'void handle(char*s){strcpy(buffer,s); unlockDoor();}\n'
            (root/'corpus/lock.cpp').write_bytes(data)
            for check in ('authorization','input_bounds','replay'):
                job=Job('j0',0,[],dict(source='corpus/lock.cpp',sha256=hashlib.sha256(data).hexdigest(),check=check))
                a=monolithic(copy.deepcopy(job),root)
                b=review(scan(prepare(copy.deepcopy(job),root)))
                self.assertEqual((a.findings,a.status),(b.findings,b.status))

    def test_hash_and_path_escape_fail(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'corpus').mkdir();(root/'corpus/a.c').write_text('safe')
            job=Job('j0',0,[],dict(source='corpus/a.c',sha256='bad',check='input_bounds'))
            with self.assertRaisesRegex(ValueError,'hash mismatch'):prepare(job,root)
            job.payload['source']='../escape.c'
            with self.assertRaisesRegex(ValueError,'escapes'):prepare(job,root)

    def test_actual_queue_topologies(self):
        rows,ingress=build_config(6,'decomposed')
        self.assertEqual(len(rows),6);self.assertEqual(ingress,[0])
        self.assertEqual([r['Server Function'] for r in rows],['Prepare','Scan','Scan','Review','Verify','Report'])
        rows,ingress=build_config(2,'monolithic')
        self.assertEqual([r['Server Function'] for r in rows],['Monolithic','VerifyReport'])

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';self.source.mkdir()
        # Intentional test
        (self.source/'lock.cpp').write_text('void handle(char*s){strcpy(buffer,s); unlockDoor();}\n')
        (self.source/'safe.h').write_text('bool isAllowed();\n')

    def generate(self,name,mode='decomposed',queues=6,extra=()):
        target=self.root/name
        subprocess.run([sys.executable,str(APP/'workload-generator/workload_generator.py'),
          '--source-root',str(self.source),'--out-dir',str(target),'--mode',mode,'--queues',str(queues),
          '--dist','fixed','--fixed-jobs','2',*extra],check=True,capture_output=True,text=True)
        return target

    def run_app(self,inputs,name,extra=()):
        out=self.root/name
        subprocess.run([sys.executable,str(APP/'main.py'),'--config',str(inputs/'qconf.csv'),
          '--out',str(out),'--workers','2','--arrival-step-seconds','0',*extra],check=True,capture_output=True,text=True)
        return out,json.loads((out/'summary.json').read_text())

    def test_csv_coverage_and_reproducibility(self):
        a=self.generate('a');b=self.generate('b')
        self.assertEqual((a/'workload.csv').read_bytes(),(b/'workload.csv').read_bytes())
        self.assertEqual((a/'jobs.jsonl').read_bytes(),(b/'jobs.jsonl').read_bytes())
        rows=list(csv.DictReader((a/'workload.csv').read_text().splitlines()))
        self.assertEqual(len(rows),6)
        self.assertEqual(list(rows[0]),['Job Id','arrival time','Tasks'])
        self.assertEqual(len(json.loads(rows[0]['Tasks'])),5)

    def test_both_modes_have_real_multiqueue_logs_and_same_results(self):
        records=[]
        for mode,n in [('decomposed',6),('monolithic',2)]:
            inputs=self.generate(mode,mode,n)
            out,summary=self.run_app(inputs,mode+'-out')
            self.assertEqual((summary['completed'],summary['analysis_errors'],summary['dropped']),(6,0,0))
            rows=list(csv.DictReader((out/'qmet.csv').read_text().splitlines()))
            self.assertEqual(len({r['queue-sys-id'] for r in rows}),n)
            for row in rows:
                self.assertGreaterEqual(int(row['num-jobs-in-queue']),0)
                self.assertIn(int(row['num-jobs-in-service']),(0,1))
            events=(out/'events.log').read_text().splitlines()
            visits=sum('ENTERS-SERVER' in line for line in events)
            self.assertEqual(visits,6*(5 if mode=='decomposed' else 2))
            self.assertEqual(sum('DEPART-VIA out.wl' in line for line in events),6)
            findings=[json.loads(line) for line in (out/'findings.jsonl').read_text().splitlines()]
            records.append(sorted((r['source'],r['check'],r['findings'],r['status']) for r in findings))
        self.assertEqual(records[0],records[1])

    def test_changed_source_is_execution_error_not_unlock(self):
        inputs=self.generate('inputs')
        (inputs/'corpus/lock.cpp').write_text('changed')
        out,summary=self.run_app(inputs,'errors')
        self.assertEqual(summary['analysis_errors'],3)
        self.assertEqual(summary['unexpected_unlocks'],0)
        self.assertIn('ERROR SERVER-FAILED',(out/'events.log').read_text())

    def test_queue_capacity_rejection_is_logged(self):
        inputs=self.generate('smallcap',extra=['--capacity','1','--passes','3'])
        out,summary=self.run_app(inputs,'drops')
        self.assertGreater(summary['dropped'],0)
        self.assertEqual(summary['completed']+summary['dropped'],summary['source_jobs'])
        self.assertIn('ERROR QUEUE-FULL',(out/'events.log').read_text())

if __name__=='__main__':unittest.main()
