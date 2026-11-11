"""Regenerate this study's plots and comparisons from saved runs."""
from run_study import study
from study_tools.report import report_hosted

if __name__ == '__main__':
    report_hosted(study)
