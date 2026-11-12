import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

QUEUE_PATH = Path(__file__).resolve().parents[1] / "queue" / "queue.py"
module = runpy.run_path(str(QUEUE_PATH))
main = module["main"]


class RemainingQueueTests(unittest.TestCase):

    def test_remove_script_directory_from_path(self):
        func = module["_load_stdlib_queue"]
        fake_sys = SimpleNamespace(path=[str(QUEUE_PATH.parent)])

        with patch.dict(func.__globals__, {"sys": fake_sys}):
            func()

        self.assertEqual(fake_sys.path, [])

    def test_path_resolution_error(self):
        func = module["_load_stdlib_queue"]
        fake_sys = SimpleNamespace(path=["/invalid-path"])

        with patch.dict(func.__globals__, {"sys": fake_sys}):
            with patch.object(Path, "resolve", side_effect=OSError("test")):
                func()

    def test_missing_grpc_dependency(self):
        with patch.dict(sys.modules, {"lib.grpc_runtime": None}):
            with self.assertRaises(SystemExit):
                main([
                    "-i", "input.csv",
                    "--outgrpc", "127.0.0.1:50052,1",
                ])

    def test_grpc_only_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = str(Path(tmp) / "output.csv")

            with patch.dict(main.__globals__, {
                "_start_listeners": lambda *args: [],
                "_wait_for_inputs": lambda *args: None,
            }):
                result = main([
                    "--ingrpc", "127.0.0.1:50051,1",
                    "-o", output,
                ])

            self.assertEqual(result, 0)