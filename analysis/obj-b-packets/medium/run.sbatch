#!/usr/bin/env bash
# Medium model: 200 routers, 420 processes, 10000 packets per run. Submit from the repository root:
#   sbatch analysis/obj-b-packets/medium/run.sbatch
# Arguments go to run_study.py. A quick test of one set:
#   sbatch --time=00:30:00 analysis/obj-b-packets/medium/run.sbatch --only base
# 32 cores and 64 GB rule out the small nodes in normal. The model needs about 20 GB.
#SBATCH --job-name=packets-medium
#SBATCH --partition=normal
#SBATCH --container=el9hw
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=64G
#SBATCH --exclusive
#SBATCH --time=02:00:00
#SBATCH --output=packets-medium-%J.out
#SBATCH --error=packets-medium-%J.out
exec bash "${SLURM_SUBMIT_DIR:-$PWD}/analysis/obj-b-packets/sooner/job.sh" medium 6000 "$@"
