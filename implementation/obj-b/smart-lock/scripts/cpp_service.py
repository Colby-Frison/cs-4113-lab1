"""Managed lifecycle for a local C++ gRPC service used by the application."""
from contextlib import contextmanager
from pathlib import Path
import socket
import subprocess

@contextmanager
def cpp_service(app,endpoint=None):
    if endpoint:
        yield endpoint;return
    binary=Path(app)/'build/cpp/smart-lock-server'
    if not binary.is_file():
        raise ValueError('Build C++ server first: bash scripts/build_cpp.sh (see README for packages)')
    
    import grpc
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    address=f'127.0.0.1:{port}'
    process=subprocess.Popen([str(binary),address])
    channel=grpc.insecure_channel(address)
    
    try:
        grpc.channel_ready_future(channel).result(timeout=15)
        yield address
    finally:
        channel.close();process.terminate()
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:process.kill();process.wait()
