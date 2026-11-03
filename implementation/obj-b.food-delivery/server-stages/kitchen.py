#!/usr/bin/env python3
"""Run a preparation-only worker over file/FIFO input and output."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fd.server import main

if __name__ == "__main__":
    main("kitchen")
