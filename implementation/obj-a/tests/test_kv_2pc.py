"""Two-phase commit against live KV replica processes."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import support  # noqa: F401

from lib.kv_2pc import TwoPhaseCoordinator
from lib.kv_store import load_kv

OBJ_A = Path(__file__).resolve().parents[1]
REPLICA = OBJ_A / "kv-store" / "replica.py"
REPO = OBJ_A.parents[1]


def _wait_listen(log_path: Path, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if log_path.exists() and "gRPC listen" in log_path.read_text(encoding="utf-8", errors="replace"):
            return
        time.sleep(0.05)
    raise TimeoutError(log_path)


def _start_replica(port: int, log: Path, store: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            str(REPLICA),
            "--listen",
            f"127.0.0.1:{port},2",
            "--logfile",
            str(log),
            "--store-kv-to",
            str(store),
        ],
        cwd=str(REPO),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


class KvTwoPhaseTests(unittest.TestCase):
    def test_commit_updates_all_replicas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ports = (29111, 29112)
            processes: list[subprocess.Popen] = []
            try:
                for index, port in enumerate(ports):
                    log = root / f"r{index}.log"
                    store = root / f"r{index}.json"
                    processes.append(_start_replica(port, log, store))
                for index in range(2):
                    _wait_listen(root / f"r{index}.log")
                coord = TwoPhaseCoordinator([f"127.0.0.1:{port}" for port in ports], timeout_sec=2.0)
                state: dict[str, int] = {}
                self.assertEqual(coord.increment(state, "j0"), 1)
                self.assertEqual(coord.increment(state, "j0"), 2)
                self.assertEqual(state, {"j0": 2})
                coord.close()
                for index in range(2):
                    path = root / f"r{index}.json"
                    deadline = time.time() + 2
                    while time.time() < deadline and load_kv(path) != {"j0": 2}:
                        time.sleep(0.05)
                    self.assertEqual(load_kv(path), {"j0": 2})
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)

    def test_prepare_failure_aborts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "r0.log"
            process = _start_replica(29113, log, root / "r0.json")
            try:
                _wait_listen(log)
                coord = TwoPhaseCoordinator(
                    ["127.0.0.1:29113", "127.0.0.1:29114"],
                    timeout_sec=0.5,
                )
                state: dict[str, int] = {}
                self.assertIsNone(coord.increment(state, "j0"))
                self.assertEqual(state, {})
                coord.close()
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
