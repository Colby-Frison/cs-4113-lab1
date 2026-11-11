#!/usr/bin/env python3
"""Collect, report, or archive this hosted food-delivery study."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from study_tools.hosted import HostedStudy

study = HostedStudy(Path(__file__).resolve().parent)

if __name__ == '__main__':
    study.main()
