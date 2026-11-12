import runpy
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "obj-a" / "server-func-stateless" / "server_function_matchmaking.py"

calls = []

mm = ModuleType("mm")
mm.__path__ = []

runtime = ModuleType("mm.runtime")


def run_stage(argv=None):
    calls.append(argv)
    return 0


runtime.run_stage = run_stage


class MatchmakingTests(unittest.TestCase):

    def setUp(self):
        calls.clear()

    def test_import_and_main(self):
        with patch.dict(sys.modules, {"mm": mm, "mm.runtime": runtime}):
            module = runpy.run_path(
                str(SOURCE),
                run_name="matchmaking_test",
            )

            self.assertEqual(module["main"](["--stage", "search"]), 0)
            self.assertEqual(calls, [["--stage", "search"]])

    def test_direct_execution(self):
        with patch.dict(sys.modules, {"mm": mm, "mm.runtime": runtime}):
            with self.assertRaises(SystemExit) as result:
                runpy.run_path(
                    str(SOURCE),
                    run_name="__main__",
                )

        self.assertEqual(result.exception.code, 0)
        self.assertEqual(calls, [None])


if __name__ == "__main__":
    unittest.main()