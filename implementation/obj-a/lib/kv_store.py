"""JSON persistence for the stateful server-function visit-count map.

The store is ``job_id -> visit count``. Load before serve; save after. A missing
file is an empty map. Writes use a temporary sibling then replace so a crash
mid-write does not leave a truncated file.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path


def load_kv(path: Path | str) -> dict[str, int]:
    """Read a JSON object of string keys to non-negative integer counts.

    A missing file returns ``{}``. Other errors raise ``ValueError`` with the path.
    """
    target = Path(path)
    if not target.exists():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{target}: cannot read KV JSON ({exc})") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{target}: KV JSON must be an object")
    state: dict[str, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ValueError(f"{target}: key {key!r} is not a string")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{target}: value for {key!r} must be an integer")
        if value < 0:
            raise ValueError(f"{target}: value for {key!r} is negative")
        state[key] = value
    return state


def save_kv(path: Path | str, state: dict[str, int]) -> None:
    """Write ``state`` as sorted JSON. Creates parent directories as needed."""
    target = Path(path)
    for key, value in state.items():
        if not isinstance(key, str):
            raise ValueError(f"KV key {key!r} is not a string")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"KV value for {key!r} must be an integer")
        if value < 0:
            raise ValueError(f"KV value for {key!r} is negative")
    target.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(dict(sorted(state.items())), indent=2, sort_keys=True) + "\n"
    # Unique temp name so concurrent gRPC workers do not collide on Windows.
    temporary = target.with_name(
        f".{target.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
    )
    temporary.write_text(body, encoding="utf-8")
    temporary.replace(target)
