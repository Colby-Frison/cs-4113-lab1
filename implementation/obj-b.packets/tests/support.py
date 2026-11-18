"""Helpers for the packet routing tests."""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
OBJ_A = APP.parent / "obj-a"
sys.path[:0] = [str(APP), str(OBJ_A)]


def load_script(path: Path, name: str):
    """Load a script from a directory whose name is not a package."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def capture_logger(name: str) -> tuple[logging.Logger, list[str]]:
    log = logging.getLogger(name)
    log.handlers.clear()
    log.setLevel(logging.INFO)
    log.propagate = False
    handler = _Capture()
    log.addHandler(handler)
    return log, handler.messages


def event_kinds(messages: list[str]) -> list[str]:
    """Event kinds in order, with an ERROR joined to its reason."""
    kinds: list[str] = []
    for message in messages:
        parts = message.split()
        if len(parts) < 5 or parts[2] != "IN":
            continue
        kinds.append(f"ERROR {parts[5]}" if parts[4] == "ERROR" else parts[4])
    return kinds
