#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
import grpc
import matplotlib
import pandas
print("deps_ok")
PY
