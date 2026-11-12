#!/usr/bin/env bash
# Run the tests on this branch.
# Usage (from anywhere):
#   ./run-all-tests.sh
#   ./run-all-tests.sh --extended
#
# The default pass runs the Objective A and matchmaking unittest suites, then
# the C++ file self-tests and K-V integration tests when their dependencies exist
#
# --extended prints the longer studies (KV smoke, the KV N x M sweep, the
# medium wait-job sanity check, and matchmaking Small through Very Large).
# Those commands are not launched. This branch is not the merge, so that
# list stays an open extension: invoke a line from show_extended when a
# longer run should actually execute.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

EXTENDED=0
for arg in "$@"; do
  case "$arg" in
    --extended) EXTENDED=1 ;;
    *)
      echo "unknown argument: $arg" >&2
      echo "usage: $0 [--extended]" >&2
      exit 2
      ;;
  esac
done

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
elif [[ -f "$ROOT/.venv/Scripts/python.exe" ]]; then
  PY="$ROOT/.venv/Scripts/python.exe"
else
  PY=python3
  command -v "$PY" >/dev/null 2>&1 || PY=python
fi

run_step() {
  local title="$1"
  shift
  echo "==> $title"
  "$@"
}

find_built() {
  local dir="$1"
  local name="$2"
  find "$dir" -type f \( -name "$name" -o -name "$name.exe" \) ! -path '*/CMakeFiles/*' -print -quit
}

run_cpp_selftests() {
  if ! command -v cmake >/dev/null 2>&1; then
    echo "==> C++ file self-tests skipped (cmake is not on PATH)"
    return 0
  fi

  local tmp jobs row sink queue_test match_test
  tmp="$(mktemp -d)"
  row='j0,0.000,"[""0: W 0.000000"", ""1: W 0.000000"", ""2: W 0.000000"", ""3: W 0.000000""]"'
  jobs="$tmp/jobs.csv"
  sink="$tmp/sink.csv"
  printf 'Job Id,arrival time,Tasks\n%s\n' "$row" >"$jobs"

  run_step "configure queue-cpp" cmake -S implementation/obj-a/queue-cpp -B implementation/obj-a/queue-cpp/build
  run_step "build queue-cpp" cmake --build implementation/obj-a/queue-cpp/build
  queue_test="$(find_built implementation/obj-a/queue-cpp/build queue_file_selftest)"
  if [[ -z "$queue_test" ]]; then
    echo "queue_file_selftest was not produced" >&2
    rm -rf "$tmp"
    exit 1
  fi
  run_step "queue_file_selftest" "$queue_test" "$jobs" "$sink"

  if [[ -n "$(find_built implementation/obj-a/queue-cpp/build queue_server)" ]]; then
    run_step "configure stateful K-V cpp" cmake -S implementation/obj-a/server-func-stateful-cpp -B implementation/obj-a/server-func-stateful-cpp/build
    run_step "build stateful K-V cpp" cmake --build implementation/obj-a/server-func-stateful-cpp/build
    run_step "stateful K-V integration tests" "$PY" implementation/obj-a/server-func-stateful-cpp/tests/test_integration.py -v
  else
    echo "==> stateful K-V integration tests skipped (gRPC C++ is unavailable)"
  fi

  run_step "configure matchmaking cpp" cmake -S implementation/obj-b.matchmaking/cpp -B implementation/obj-b.matchmaking/cpp/build
  run_step "build matchmaking cpp" cmake --build implementation/obj-b.matchmaking/cpp/build
  match_test="$(find_built implementation/obj-b.matchmaking/cpp/build match_file_selftest)"
  if [[ -z "$match_test" ]]; then
    echo "match_file_selftest was not produced" >&2
    rm -rf "$tmp"
    exit 1
  fi
  run_step "match_file_selftest" "$match_test" "$row" "$tmp/match-sink.csv"
  rm -rf "$tmp"
}

show_extended() {
  # Open on purpose. Nothing in this list is started.
  # To close one case, replace the echo of that line with a call.
  echo "==> extended testing left open (not launched)"
  echo "    This branch is not planned for merge. These are the longer runs we had in mind."
  local commands=(
    'python implementation/obj-a/kv-store/smoke_check.py --jobs 24 --configs "1,1;2,1;1,2;2,2"'
    'python implementation/obj-a/kv-store/studies/run_kv_study.py'
    'python implementation/obj-a/sanity-check/medium/run_medium.py'
    'python implementation/obj-b.matchmaking/studies/run_study.py --size small'
    'python implementation/obj-b.matchmaking/studies/run_study.py --size medium'
    'python implementation/obj-b.matchmaking/studies/run_study.py --size large'
    'python implementation/obj-b.matchmaking/studies/run_study.py --size very-large'
    'bash implementation/obj-b.matchmaking/studies/remote/run_medium.sh'
    'bash implementation/obj-b.matchmaking/studies/remote/run_large.sh'
    'bash implementation/obj-b.matchmaking/studies/remote/run_very_large.sh'
  )
  local command
  for command in "${commands[@]}"; do
    echo "    $command"
  done
}

run_step "Objective A unit tests" "$PY" -m unittest discover -s implementation/obj-a/tests -v
run_step "matchmaking unit tests" "$PY" -m unittest discover -s implementation/obj-b.matchmaking/tests -v
run_cpp_selftests

if [[ "$EXTENDED" -eq 1 ]]; then
  show_extended
fi

echo "==> all tests finished"
echo "    Extended studies stay open. Pass --extended to list them; they are not run."
