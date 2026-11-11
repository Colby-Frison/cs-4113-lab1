"""Protect raw study evidence when using the shared archive helpers."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'analysis/simple/obj-b-food-delivery'))
from study_tools.files import archive, saved_run


class StudyArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.here = Path(temporary.name)
        self.runs = self.here / 'runs'
        self.results = self.here / 'results'
        self.root = self.runs / 'baseline/seed-1'
        (self.root / 'analysis').mkdir(parents=True)
        (self.root / 'analysis/summary.json').write_text(json.dumps({'delivered': 1}))
        self.payload = b'order,original record\n'
        (self.root / 'sink.csv').write_bytes(self.payload)
        self.spec = {'scenarios': [{'name': 'baseline'}], 'seeds': [1]}

    def archive(self):
        archive(self.here, self.results, self.runs, self.spec)

    def test_archive_roundtrip_and_repeat(self):
        self.archive()
        self.assertFalse(self.root.exists())
        manifest = (self.results / 'archive-manifest.csv').read_bytes()
        self.archive()
        self.assertEqual(manifest, (self.results / 'archive-manifest.csv').read_bytes())
        with saved_run(self.runs, 'baseline', 1) as restored:
            self.assertEqual((restored / 'sink.csv').read_bytes(), self.payload)

    def test_mismatched_archive_preserves_source(self):
        self.archive()
        self.root.mkdir()
        source = self.root / 'sink.csv'
        source.write_bytes(b'new evidence must survive\n')
        with self.assertRaisesRegex(RuntimeError, 'archive differs from source'):
            self.archive()
        self.assertEqual(source.read_bytes(), b'new evidence must survive\n')


if __name__ == '__main__':
    unittest.main()
