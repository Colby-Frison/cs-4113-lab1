"""Helpers for the firewall tests. They reuse the packet routing test helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

FIREWALL = Path(__file__).resolve().parents[1]
PACKETS_TESTS = FIREWALL.parent / "obj-b.packets" / "tests"

# Both test folders have a support.py, so load the packet routing one under its own name.
# Loading it puts the packet routing app and Objective A on the path.
_spec = importlib.util.spec_from_file_location("packets_support", PACKETS_TESTS / "support.py")
_packets = importlib.util.module_from_spec(_spec)
sys.modules["packets_support"] = _packets
_spec.loader.exec_module(_packets)
sys.path[:0] = [str(FIREWALL), str(PACKETS_TESTS)]

APP = _packets.APP
capture_logger = _packets.capture_logger
event_kinds = _packets.event_kinds
load_script = _packets.load_script
