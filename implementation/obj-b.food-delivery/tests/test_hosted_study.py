"""Study-size expansion and protection against mixing saved experiments."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
STUDIES = REPO / "analysis/simple/obj-b-food-delivery"
sys.path.insert(0, str(STUDIES))
sys.path.insert(0, str(REPO / "implementation/obj-b.food-delivery"))
from study_tools.hosted import HostedStudy
from fd.network_config import validate_config
from run_hosted import run


class HostedStudyTests(unittest.TestCase):
    def setUp(self):
        self.study = HostedStudy(STUDIES / "very-large")

    def test_very_large_topology_and_paired_workloads(self):
        spec = self.study.spec
        self.assertEqual(spec["seeds"], [1])
        self.assertEqual(spec["baseline"]["count"] * len(spec["scenarios"]), 700000)
        self.assertEqual(len(spec["baseline"]["restaurant_regions"]), 1500)
        self.assertEqual(len(spec["baseline"]["couriers"]), 500)
        counts = [2000, 2500, 3000, 4000, 1800, 1600, 1400]
        for scenario, total in zip(spec["scenarios"], counts):
            config = self.study.configuration(scenario, 1)
            validate_config(config)
            self.assertEqual(sum(config["couriers"]), total)
            self.assertEqual(
                {k: v for k, v in config.items() if k not in ("couriers", "seed")},
                {k: v for k, v in spec["baseline"].items() if k != "couriers"},
            )

    def test_pilot_is_separate_and_saved_parameters_cannot_change(self):
        with self.assertRaisesRegex(ValueError, "Use --output"):
            self.study.configure(smoke=True)
        with tempfile.TemporaryDirectory() as tmp:
            self.study.configure(tmp, smoke=True)
            self.assertEqual(len(self.study.spec["scenarios"]), 1)
            self.assertEqual(self.study.spec["baseline"]["count"], 5000)
            self.study.runs.mkdir()
            (self.study.runs / "study.json").write_text(json.dumps(self.study.spec))
            with self.assertRaisesRegex(ValueError, "saved experiment differs"):
                self.study.configure(tmp, count=6000)

    def test_low_file_limit_fails_before_creating_run_files(self):
        config = self.study.configuration(self.study.spec["scenarios"][0], 1)
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("run_hosted.resource.getrlimit", return_value=(1024, 8192)),
        ):
            with self.assertRaisesRegex(ValueError, "ulimit -n"):
                run(tmp, config)
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
