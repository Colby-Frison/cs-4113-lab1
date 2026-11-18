"""Archive this study's raw runs after verifying every member's bytes."""

from pathlib import Path
import sys
from run_study import HERE, RESULTS, RUNS, SPEC

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from study_tools.files import archive as archive_runs


def archive():
    archive_runs(HERE, RESULTS, RUNS, SPEC)


if __name__ == "__main__":
    archive()
