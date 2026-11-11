#!/usr/bin/env python3
"""Run a delivery-only worker; fleet-wide state belongs to the dispatcher."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fd.server import main

if __name__ == "__main__":
    main("courier")
