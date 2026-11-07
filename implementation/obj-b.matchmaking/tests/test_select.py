"""Unit tests for run_study --only group tokens."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
STUDIES = HERE.parent / "studies"
OBJ_A = HERE.parents[1] / "obj-a"
for entry in (str(HERE.parent), str(OBJ_A), str(STUDIES)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from studies.run_study import ONLY_GROUPS, _select  # noqa: E402


class SelectTests(unittest.TestCase):
    def test_groups_cover_cuts_and_boosts(self) -> None:
        self.assertEqual(ONLY_GROUPS["srv-"], ("srv-10", "srv-20", "srv-30"))
        self.assertEqual(ONLY_GROUPS["srv+"], ("srv+25", "srv+50", "srv+100"))

    def test_srv_minus_group(self) -> None:
        names = [name for name, _factor in _select("srv-")]
        self.assertEqual(names, ["srv-10", "srv-20", "srv-30"])

    def test_srv_plus_group(self) -> None:
        names = [name for name, _factor in _select("srv+")]
        self.assertEqual(names, ["srv+25", "srv+50", "srv+100"])

    def test_mixed_names_and_groups_keep_order(self) -> None:
        names = [name for name, _factor in _select("srv+,base,srv-10")]
        self.assertEqual(names, ["base", "srv-10", "srv+25", "srv+50", "srv+100"])

    def test_unknown_token_exits(self) -> None:
        with self.assertRaises(SystemExit):
            _select("srv-99")


if __name__ == "__main__":
    unittest.main()
