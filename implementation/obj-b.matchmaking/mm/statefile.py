"""Visit counts that have to survive a file-at-a-time ready-check pass."""

from __future__ import annotations

from pathlib import Path


def load_state(path: Path) -> dict[str, int]:
    """Read ``job_id visits`` lines. A missing file is an empty count."""
    if not path.exists():
        return {}
    state: dict[str, int] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            raise ValueError(f"{path}:{line_number} is not 'job_id visits'")
        job_id, count_text = parts
        try:
            count = int(count_text)
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number} has a non-integer visit count") from exc
        if count < 0:
            raise ValueError(f"{path}:{line_number} has a negative visit count")
        state[job_id] = count
    return state


def save_state(path: Path, state: dict[str, int]) -> None:
    """Rewrite the file, sorted by job id, so a later process sees this count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{job_id} {state[job_id]}\n" for job_id in sorted(state))
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(body, encoding="utf-8")
    temporary.replace(path)
