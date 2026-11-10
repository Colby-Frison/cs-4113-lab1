#!/usr/bin/env bash
set -euo pipefail
app_dir="$(cd "$(dirname "$0")/.." && pwd)"
cmake -S "$app_dir/cpp" -B "$app_dir/build/cpp" -DCMAKE_BUILD_TYPE=Release
cmake --build "$app_dir/build/cpp" -j 2
# A generated Python message module is bundled. C++ generated files use this
# machine's matching protoc/libprotobuf, no grpcio-tools package is required.
echo "Built $app_dir/build/cpp/smart-lock-server"
