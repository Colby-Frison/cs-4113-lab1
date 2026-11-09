#!/usr/bin/env python3
"""Regenerate lib/proto_gen from the .proto files.

Requires grpcio-tools (see ../requirements.txt). Runtime only needs grpcio.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROTO_NAMES = (
    "job.proto",
    "queue_to_server.proto",
    "server_to_queue.proto",
    "queue_to_queue.proto",
    "kv_replica.proto",
)

# grpc_tools writes absolute imports. The stubs live in a package, so they
# have to be relative or `python -m` cannot find job_pb2.
_STUB_MODULES = (
    "job_pb2",
    "queue_to_server_pb2",
    "server_to_queue_pb2",
    "queue_to_queue_pb2",
    "kv_replica_pb2",
)


def main() -> int:
    proto_dir = Path(__file__).resolve().parent
    out_dir = proto_dir.parent / "lib" / "proto_gen"
    out_dir.mkdir(parents=True, exist_ok=True)
    init = out_dir / "__init__.py"
    if not init.exists():
        init.write_text('"""Generated from ../proto. Run proto/generate_stubs.py to refresh."""\n', encoding="utf-8")

    command = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"-I{proto_dir}",
        f"--python_out={out_dir}",
        f"--grpc_python_out={out_dir}",
        *[str(proto_dir / name) for name in PROTO_NAMES],
    ]
    subprocess.check_call(command)
    for path in out_dir.glob("*_pb2*.py"):
        text = path.read_text(encoding="utf-8")
        for module in _STUB_MODULES:
            text = text.replace(f"import {module} as ", f"from . import {module} as ")
        path.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
