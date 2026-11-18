import hashlib
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "server-func-stateless" / "server_function.py"

scanner = ModuleType("scanner")
scanner.clean_source = lambda code: code.strip()
scanner.find_candidates = lambda code, check: ["match"] if "vulnerable" in code else []

with patch.dict(sys.modules, {"scanner": scanner}):
    app = runpy.run_path(str(SOURCE), run_name="smart_lock_test")


class SmartLockTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "corpus").mkdir()

        self.data = b"vulnerable code"
        (self.root / "corpus" / "main.cpp").write_bytes(self.data)

        self.job = SimpleNamespace(
            payload={
                "source": "corpus/main.cpp",
                "sha256": hashlib.sha256(self.data).hexdigest(),
                "check": "test",
            },
            prepared="",
            findings=[],
            status="",
        )

    def test_read_source(self):
        self.assertEqual(
            app["read_source"](self.job, self.root),
            self.data.decode(),
        )

    def test_source_outside_corpus(self):
        self.job.payload["source"] = "../outside.cpp"
        with self.assertRaises(ValueError):
            app["read_source"](self.job, self.root)

    def test_hash_mismatch(self):
        self.job.payload["sha256"] = "invalid"
        with self.assertRaises(ValueError):
            app["read_source"](self.job, self.root)

    def test_invalid_utf8(self):
        data = b"\xff"
        (self.root / "corpus" / "main.cpp").write_bytes(data)
        self.job.payload["sha256"] = hashlib.sha256(data).hexdigest()
        self.assertEqual(app["read_source"](self.job, self.root), "\ufffd")

    def test_prepare(self):
        self.assertIs(app["prepare"](self.job, self.root), self.job)
        self.assertEqual(self.job.prepared, "vulnerable code")

    def test_scan(self):
        self.job.prepared = "vulnerable code"
        self.assertIs(app["scan"](self.job), self.job)
        self.assertEqual(self.job.findings, ["match"])
        self.assertEqual(self.job.prepared, "")

    def test_review(self):
        self.job.findings = ["match"]
        app["review"](self.job)
        self.assertEqual(self.job.status, "candidate")

        self.job.findings = []
        app["review"](self.job)
        self.assertEqual(self.job.status, "no_candidate")

    def test_report(self):
        self.assertIs(app["report"](self.job), self.job)

    def test_monolithic(self):
        self.assertIs(app["monolithic"](self.job, self.root), self.job)
        self.assertEqual(self.job.status, "candidate")

        self.data = b"safe code"
        (self.root / "corpus" / "main.cpp").write_bytes(self.data)
        self.job.payload["sha256"] = hashlib.sha256(self.data).hexdigest()

        app["monolithic"](self.job, self.root)
        self.assertEqual(self.job.status, "no_candidate")

    def test_execute_stages(self):
        for stage in ["Prepare", "Scan", "Review", "Report", "Monolithic"]:
            with self.subTest(stage=stage):
                result = app["execute"](stage, self.job, self.root)
                self.assertIs(result, self.job)

    def test_execute_invalid_stage(self):
        with self.assertRaises(ValueError):
            app["execute"]("Invalid", self.job, self.root)

    def test_execute_cpp_grpc(self):
        rpc = ModuleType("rpc_client")
        calls = []

        def execute_rpc(*args):
            calls.append(args)
            return self.job

        rpc.execute_rpc = execute_rpc

        with patch.dict(sys.modules, {"rpc_client": rpc}):
            result = app["execute"](
                "Scan",
                self.job,
                self.root,
                engine="cpp-grpc",
                endpoint="localhost:50051",
                rpc_timeout=30,
            )

        self.assertIs(result, self.job)
        self.assertEqual(calls[0][0], "Scan")
        self.assertEqual(calls[0][3], "localhost:50051")

    def test_source_path_exception(self):
        with patch.object(Path, "is_relative_to", side_effect=OSError("path error")):
            with self.assertRaises(OSError):
                app["read_source"](self.job, self.root)

    def test_hash_exception(self):
        with patch.object(app["read_source"].__globals__["hashlib"], "sha256",
                          side_effect=RuntimeError("hash error")):
            with self.assertRaises(RuntimeError):
                app["read_source"](self.job, self.root)


if __name__ == "__main__":
    unittest.main()