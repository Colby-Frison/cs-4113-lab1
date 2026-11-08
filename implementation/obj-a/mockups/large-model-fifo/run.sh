#!/usr/bin/env bash
# Three-AS stand-in for the Lab 0 LARGE model, using FIFOs instead of gRPC.
# Run from WSL or Linux. mkfifo does not exist in PowerShell.
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "FIFOs need Linux or WSL. This shell is $(uname -s)." >&2
    exit 1
fi

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../../.." && pwd)
RUN="$HERE/run"
mkdir -p "$RUN"
rm -f "$RUN"/*

if command -v python3 >/dev/null 2>&1; then
    PY=python3
else
    PY=python
fi

GEN="$REPO/implementation/obj-a/workload-generator/workload_generator.py"
QUEUE="$REPO/implementation/obj-a/queue/queue.py"
STATELESS="$REPO/implementation/obj-a/server-func-stateless/server_func.py"
STATEFUL="$REPO/implementation/obj-a/server-func-stateful/server_func.py"

JOBS="$RUN/jobs.csv"
SINK="$RUN/sink.csv"
COUNT=6

FIFO_DIR=$(mktemp -d /tmp/obj-a-large-fifo.XXXXXX)
Q0_SF="$FIFO_DIR/q0_sf"
SF_Q1="$FIFO_DIR/sf_q1"
Q1_Q2="$FIFO_DIR/q1_q2"
Q2_SF="$FIFO_DIR/q2_sf"
mkfifo "$Q0_SF" "$SF_Q1" "$Q1_Q2" "$Q2_SF"

pids=()
timer=""

cleanup() {
    local status=$?
    if [[ -n "$timer" ]]; then
        kill "$timer" 2>/dev/null || true
    fi
    if (( status != 0 )); then
        for pid in "${pids[@]+"${pids[@]}"}"; do
            kill "$pid" 2>/dev/null || true
        done
    fi
    rm -rf "$FIFO_DIR"
}
trap cleanup EXIT

start() {
    local name=$1
    shift
    "$@" >"$RUN/$name.stdout" 2>"$RUN/$name.stderr" &
    pids+=("$!")
}

"$PY" "$GEN" \
    --arrival-dist fixed --arrival-fixed 0 \
    --wait-dist uniform --wait-low 0.01 --wait-high 0.05 \
    --tasks 1 --count "$COUNT" --seed 7 \
    --out "$JOBS"

# Sink end first. Each open blocks until the other end of that pipe exists.
start sf-stateful "$PY" "$STATEFUL" \
    --qid q2 --server-id 0 \
    -i "$Q2_SF" -o "$SINK" \
    --logfile "$RUN/sf-stateful.log"
start q2 "$PY" "$QUEUE" \
    --qid q2 --qsize 8 \
    -i "$Q1_Q2" -o "$Q2_SF" \
    --logfile "$RUN/q2.log"
start q1 "$PY" "$QUEUE" \
    --qid q1 --qsize 8 \
    -i "$SF_Q1" -o "$Q1_Q2" \
    --logfile "$RUN/q1.log"
start sf-stateless "$PY" "$STATELESS" \
    --qid q0 --server-id 0 \
    -i "$Q0_SF" -o "$SF_Q1" \
    --logfile "$RUN/sf-stateless.log"
start q0 "$PY" "$QUEUE" \
    --qid q0 --qsize 8 \
    -i "$JOBS" -o "$Q0_SF" \
    --logfile "$RUN/q0.log"

(
    sleep 45
    for pid in "${pids[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
) &
timer=$!

fail=0
for pid in "${pids[@]}"; do
    if ! wait "$pid"; then
        fail=1
    fi
done
kill "$timer" 2>/dev/null || true
wait "$timer" 2>/dev/null || true
timer=""

if (( fail != 0 )); then
    echo "a pipeline process exited with an error. See $RUN/*.stderr" >&2
    exit 1
fi

count_text() {
    local path=$1
    local needle=$2
    if [[ ! -f "$path" ]]; then
        echo 0
        return
    fi
    grep -F -c "$needle" "$path" || true
}

echo "jobs: $COUNT"
echo "q0 ARRIVE-VIA: $(count_text "$RUN/q0.log" ARRIVE-VIA)"
echo "q0 DEPART-VIA: $(count_text "$RUN/q0.log" DEPART-VIA)"
echo "stateless ENTERS-SERVER: $(count_text "$RUN/sf-stateless.log" ENTERS-SERVER)"
echo "q1 ARRIVE-VIA: $(count_text "$RUN/q1.log" ARRIVE-VIA)"
echo "q1 DEPART-VIA: $(count_text "$RUN/q1.log" DEPART-VIA)"
echo "q2 ARRIVE-VIA: $(count_text "$RUN/q2.log" ARRIVE-VIA)"
echo "q2 DEPART-VIA: $(count_text "$RUN/q2.log" DEPART-VIA)"
echo "stateful ENTERS-SERVER: $(count_text "$RUN/sf-stateful.log" ENTERS-SERVER)"
echo "stateful state-visits: $(count_text "$RUN/sf-stateful.log" state-visits)"
qfull=$((
    $(count_text "$RUN/q0.log" QUEUE-FULL) +
    $(count_text "$RUN/q1.log" QUEUE-FULL) +
    $(count_text "$RUN/q2.log" QUEUE-FULL)
))
echo "QUEUE-FULL: $qfull"
echo "sink: $SINK"

ANALYZE="$REPO/implementation/obj-a/mockups/analyze_run.py"
if "$PY" -c "import pandas, numpy, matplotlib" >/dev/null 2>&1; then
    "$PY" "$ANALYZE" "$RUN"
else
    echo "analysis skipped: $PY is missing pandas, numpy, or matplotlib" >&2
fi
