#!/usr/bin/env bash
# The body of every model's Slurm job. Each model's run.sbatch sets the resources and
# calls this from the repository root:
#   job.sh MODEL NEED_TASKS [run_study.py arguments]
# MODEL is a packet routing model such as simple, or a folder under analysis/ such as
# obj-b-firewall/simple. NEED_TASKS is a rough count of the processes and threads the
# model starts. The job stops at once if the node's limits are lower, so a run doesn't
# fail an hour in.
set -uo pipefail

MODEL="$1"
NEED_TASKS="$2"
shift 2
JOB_ID="${SLURM_JOB_ID:-local}"
REPO="${SLURM_SUBMIT_DIR:-$PWD}"
STUDIES="$REPO/analysis/obj-b-packets"
case "$MODEL" in
    */*) MODEL_DIR="$REPO/analysis/$MODEL"; TAG="${MODEL//\//-}" ;;
    *) MODEL_DIR="$STUDIES/$MODEL"; TAG="packets-$MODEL" ;;
esac
# FIFOs and per-line log flushes stay off the shared filesystem during a run.
WORK="${PACKETS_WORK:-/tmp/$TAG-$JOB_ID}"
# Each run's raw logs are packed here as soon as the run is analyzed.
ARCHIVE="${PACKETS_ARCHIVE:-/scratch/$USER/$TAG/$JOB_ID}"
PYTHON="${PACKETS_PYTHON:-$HOME/packets-venv/bin/python}"

fail() {
    echo "$1" >&2
    exit "$2"
}

[ -f "$MODEL_DIR/study.json" ] || fail "Submit from the repository root. Looked for $MODEL_DIR/study.json" 2
if command -v module >/dev/null 2>&1; then
    module purge
    module load Python/3.13.5-GCCcore-14.3.0
fi
[ -x "$PYTHON" ] || fail "No Python at $PYTHON. Run analysis/obj-b-packets/sooner/setup.sh first." 2

# Thousands of processes need the soft limits raised as far as the node allows.
ulimit -n "$(ulimit -Hn)" 2>/dev/null
ulimit -u "$(ulimit -Hu)" 2>/dev/null

echo "model $MODEL, host $(hostname), job $JOB_ID, started $(date)"
echo "cores $(nproc), max user processes $(ulimit -u), max open files $(ulimit -n)"
echo "kernel pid_max $(cat /proc/sys/kernel/pid_max), threads-max $(cat /proc/sys/kernel/threads-max)"
echo "ephemeral ports $(cat /proc/sys/net/ipv4/ip_local_port_range)"
free -g | sed -n 2p

limit="$(ulimit -u)"
if [ "$limit" != "unlimited" ] && [ "$limit" -lt "$NEED_TASKS" ]; then
    fail "This model needs about $NEED_TASKS processes and threads, but the limit is $limit. Ask support@oscer.ou.edu to raise it for the job." 4
fi
for kernel_limit in pid_max threads-max; do
    value="$(cat /proc/sys/kernel/$kernel_limit)"
    [ "$value" -ge "$NEED_TASKS" ] || fail "kernel $kernel_limit is $value, below the $NEED_TASKS this model needs." 4
done
low_port="$(cut -f1 /proc/sys/net/ipv4/ip_local_port_range)"
[ "$low_port" -ge 32000 ] || fail "Ephemeral ports start at $low_port and would collide with queue ports 20000 to 31999." 4

mkdir -p "$WORK" "$ARCHIVE"
df -h "$WORK" | tail -1
mkfifo "$WORK/fifo-check" || fail "$WORK does not support FIFOs. Set PACKETS_WORK to a directory that does." 3
rm -f "$WORK/fifo-check"
# Past 6000 queues, a run listens on 127.0.0.2 and up as well as 127.0.0.1.
queues="$("$PYTHON" -c "import json, sys; s = json.load(open(sys.argv[1])); print(s['routers'] * (3 if s.get('mode') == 'stages' else 1))" "$MODEL_DIR/study.json")"
echo "queues $queues"
if [ "$queues" -gt 6000 ]; then
    "$PYTHON" -c "import socket; s = socket.socket(); s.bind(('127.0.0.2', 0)); s.close()" \
        || fail "Can't listen on 127.0.0.2, which models over 6000 queues need." 3
fi

"$PYTHON" "$STUDIES/run_study.py" "$MODEL" --work-dir "$WORK/runs" --archive "$ARCHIVE" "$@"
status=$?

rm -rf "$WORK"
echo "raw logs: $ARCHIVE"
echo "finished $(date), status $status"
exit "$status"
