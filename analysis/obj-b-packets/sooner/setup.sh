#!/usr/bin/env bash
# One-time setup on Sooner. Run on a login node, from the repository root:
#   bash analysis/obj-b-packets/sooner/setup.sh
set -euo pipefail

module purge
module load Python/3.13.5-GCCcore-14.3.0

python -m venv "$HOME/packets-venv"
"$HOME/packets-venv/bin/pip" install --upgrade pip
"$HOME/packets-venv/bin/pip" install -r implementation/obj-a/requirements.txt numpy pandas matplotlib

"$HOME/packets-venv/bin/python" -c "import grpc, numpy, pandas, matplotlib; print('grpcio', grpc.__version__)"
# The tests start real networks, so they don't run here on a login node.
echo "Setup finished. Submit a job from the repository root."
