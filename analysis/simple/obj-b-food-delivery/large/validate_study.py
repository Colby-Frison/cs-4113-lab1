"""Audit this study's saved logs, metrics, and plots."""

import argparse
from run_study import study
from study_tools.validate import validate_hosted

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    args = parser.parse_args()
    study.configure(args.output)
    validate_hosted(study)
