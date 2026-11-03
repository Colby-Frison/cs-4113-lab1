"""POSIX FIFO admit edge for a study run.

A regular file returns EOF once the reader catches the writer, which dumps
the rest of the tickets at once. The FIFO stays open until the generator
closes it, so arrivals keep their interarrival sleep. Studies run on Linux
(VirtualBox or WSL locally, remotes for the large bands). The FIFO itself
is created under a native temp directory so a Windows-mounted repo path
(DrvFs) does not have to support ``mkfifo``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from pathlib import Path


class JobPipe:
    """Byte pipe. Admit opens ``path`` for read; this object writes the generator stream."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        self._file = None
        self._tmpdir: Path | None = None
        if path is None:
            self._tmpdir = Path(tempfile.mkdtemp(prefix="mm-fifo-"))
            self.path = self._tmpdir / "jobs.fifo"
        else:
            self.path = Path(path)
            if self.path.exists():
                self.path.unlink()
        try:
            os.mkfifo(self.path, 0o666)
        except OSError as exc:
            if self._tmpdir is not None:
                shutil.rmtree(self._tmpdir, ignore_errors=True)
                self._tmpdir = None
            raise OSError(
                f"mkfifo {self.path} failed ({exc}). "
                "Use a native Linux filesystem for the admit FIFO."
            ) from exc

    def wait_for_reader(self, timeout: float) -> None:
        """Open for write once admit is reading, or raise when ``timeout`` passes."""
        if self._file is not None:
            return
        box: dict[str, object] = {}

        def open_writer() -> None:
            try:
                box["file"] = self.path.open("w", encoding="utf-8", buffering=1)
            except OSError as exc:
                box["error"] = exc

        thread = threading.Thread(target=open_writer, name="fifo-open")
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            raise TimeoutError(f"{self.path} had no reader after {timeout:.0f}s")
        if "error" in box:
            raise OSError(f"open {self.path} for write failed: {box['error']}")
        with self._lock:
            self._file = box["file"]

    def write(self, text: str) -> int:
        with self._lock:
            handle = self._file
        if handle is None:
            raise OSError("job pipe is not open for write")
        handle.write(text)
        handle.flush()
        return len(text)

    def flush(self) -> None:
        with self._lock:
            handle = self._file
        if handle is not None:
            handle.flush()

    def close(self) -> None:
        with self._lock:
            handle = self._file
            self._file = None
        if handle is not None:
            handle.close()
        if self._tmpdir is not None:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None


class Tee:
    """Write the generator stream to ``jobs.csv`` and to the FIFO admit reads."""

    def __init__(self, saved: Path, pipe: JobPipe) -> None:
        self._file = saved.open("w", encoding="utf-8", newline="")
        self._pipe = pipe

    def write(self, text: str) -> int:
        self._file.write(text)
        self._file.flush()
        self._pipe.write(text)
        return len(text)

    def flush(self) -> None:
        self._file.flush()

    def close(self) -> None:
        self._file.close()
        self._pipe.close()
