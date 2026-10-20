"""Helpers for the Objective A unit tests."""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class CaptureHandler(logging.Handler):
    """Collect formatted log messages without touching the root logger."""

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
    handler = CaptureHandler()
    log.addHandler(handler)
    return log, handler.messages


def load_script(path: Path, name: str):
    """Load a script that lives in a directory whose name is not a package."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def event_kinds(messages: list[str]) -> list[str]:
    """Return Lab 0 kinds, with ERROR combined with its reason."""
    kinds: list[str] = []
    for message in messages:
        parts = message.split()
        if len(parts) < 5 or parts[2] != "IN":
            continue
        if parts[4] == "ERROR":
            kinds.append(f"ERROR {parts[5]}")
        else:
            kinds.append(parts[4])
    return kinds
