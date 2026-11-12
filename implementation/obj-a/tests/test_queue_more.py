"""Additional behavioral tests for the Objective A queue."""
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock, patch

from support import ROOT, load_script
from lib.grpc_spec import parse_listen
from lib.job import Job, Task, format_job_line, parse_job_line

QUEUE = load_script(ROOT / 'queue' / 'queue.py', 'obj_a_queue_more')

def job():
    return parse_job_line(format_job_line('j0', 1.0, (Task(0, 0.25),)))

class MoreQueueTests(unittest.TestCase):
    def test_producer_skips_header_blank_and_bad_lines(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'jobs.csv'
            f.write_text('job_id,arrival_time,tasks\n\nbad csv\n' + job().raw_line + '\n')
            b = QUEUE.Queue()
            QUEUE.worker_producer(Barrier(1), b, 0, str(f), 'q0')
            self.assertEqual(b.get_nowait().job_id, 'j0')
            self.assertTrue(b.empty())

    def test_no_queue_id(self):
        with self.assertRaises(SystemExit):
            QUEUE.main(['--qid', '', '-i', 'input.csv', '-o', 'out.csv'])

    def test_default_unbounded_buffer(self):
        with tempfile.TemporaryDirectory() as d:
            inp = Path(d)/'in.csv'
            out = Path(d)/'out.csv'
            inp.write_text(job().raw_line + '\n')
            self.assertEqual(QUEUE.main(['-i',str(inp),'-o',str(out)]), 0)
            self.assertEqual(out.read_text().strip(), job().raw_line)

    def test_grpc_sender_threads(self):
        import types
        with tempfile.TemporaryDirectory() as d:
            inp = Path(d)/'in.csv'
            inp.write_text(job().raw_line+'\n')
            submit = Mock(return_value='accepted')
            fake = types.ModuleType('lib.grpc_runtime')
            fake.submit_job = submit
            fake.start_queue_server = Mock()
            with patch.dict(sys.modules, {'lib.grpc_runtime': fake}):
                self.assertEqual(QUEUE.main(['-i',str(inp),'--outgrpc','127.0.0.1:45678,2']), 0)
            self.assertTrue(submit.called)

    def test_listener_and_shutdown(self):
        server = Mock()
        seen = []
        def start(spec, admit):
            seen.append(admit(job(), 'origin'))
            return server, 12345
        b = QUEUE.Queue()
        listeners = QUEUE._start_listeners([parse_listen('127.0.0.1:12345,1')], b, 'q0', start)
        self.assertEqual(len(listeners), 1)
        self.assertEqual(seen, [True])
        self.assertEqual(b.get_nowait().job_id, 'j0')
        with patch.object(QUEUE.time, 'sleep', side_effect=KeyboardInterrupt):
            QUEUE._wait_for_inputs([], listeners)
        server.stop.assert_called_once_with(1)

    def test_sidecar_more_than_one_sample(self):
        import threading
        b = QUEUE.Queue()
        barrier = Barrier(2)
        sampled = threading.Event()
        count = 0
        def sleep(_):
            nonlocal count
            count += 1
            if count >= 2:
                sampled.set()
            threading.Event().wait(0.005)
        with patch.object(QUEUE.time, 'sleep', side_effect=sleep):
            t = threading.Thread(target=QUEUE.worker_sidecar, args=(b,barrier,0.001))
            t.start()
            self.assertTrue(sampled.wait(timeout=2))
            barrier.wait(timeout=2)
            t.join(timeout=2)
            self.assertFalse(t.is_alive())

    def test_cli_main_branch_in_subprocess(self):
        result = subprocess.run([sys.executable, str(ROOT/'queue'/'queue.py'), '--help'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('usage:', result.stdout)
