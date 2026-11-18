import runpy
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ServerWrapperTests(unittest.TestCase):

    def test_stateful_import(self):
        module = runpy.run_path(
            str(ROOT / "server-func-stateful" / "server_func.py"),
            run_name="server_func_stateful",
        )
        self.assertIn("main", module)

    def test_stateless_import(self):
        module = runpy.run_path(
            str(ROOT / "server-func-stateless" / "server_func.py"),
            run_name="server_func_stateless",
        )
        self.assertIn("main", module)