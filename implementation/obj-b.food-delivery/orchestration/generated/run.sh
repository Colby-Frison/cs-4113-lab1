#!/bin/sh
set -eu
cd "$(dirname "$0")"
transport=${1:-grpc}
case "$transport" in fifo|grpc) ;; *) echo "usage: $0 [fifo|grpc]" >&2; exit 2 ;; esac
compose="compose.$transport.yaml"
mkdir -p "../runs/$transport"
if [ -d "../runs/$transport/run" ]; then
    echo "move ../runs/$transport/run aside before starting a new run" >&2
    exit 1
fi
docker build -t food-delivery:local -f ../Dockerfile ../../../..
cleanup() {
    docker compose -f "$compose" logs --no-color > "../runs/$transport/compose.log" 2>&1 || true
    docker compose -f "$compose" down --volumes
}
trap cleanup EXIT
# start the queues and workers before injecting the finite workload
docker compose -f "$compose" up -d --wait --wait-timeout 60 q0 q1 kitchen-0 kitchen-1 courier-0 courier-1 courier-2
docker compose -f "$compose" run --rm --no-deps generator
