"""Optionally compress raw logs after verifying their contents."""
from run_study import study
from study_tools.files import archive

if __name__ == '__main__':
    archive(study.here, study.results, study.runs, study.spec)
