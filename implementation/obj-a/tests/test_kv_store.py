"""JSON KV load/store for the stateful visit map."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401  — puts implementation/obj-a on sys.path

from lib.kv_store import load_kv, save_kv


class KvStoreTests(unittest.TestCase):
    def test_missing_file_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_kv(Path(tmp) / "missing.json"), {})

    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kv.json"
            save_kv(path, {"j1": 2, "j0": 1})
            self.assertEqual(load_kv(path), {"j0": 1, "j1": 2})
            text = path.read_text(encoding="utf-8")
            self.assertIn('"j0"', text)
            self.assertIn('"j1"', text)

    def test_bad_json_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError) as raised:
                load_kv(path)
            self.assertIn(str(path), str(raised.exception))

    def test_non_object_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "list.json"
            path.write_text("[1, 2]", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_kv(path)

    def test_negative_value_rejected_on_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                save_kv(Path(tmp) / "kv.json", {"j0": -1})


if __name__ == "__main__":
    unittest.main()
