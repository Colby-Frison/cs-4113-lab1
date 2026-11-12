#!/usr/bin/env bash
# Large model: 1000 routers, 2100 processes, 100000 packets per run. Submit from the repository root:
#   sbatch analysis/obj-b-packets/large/run.sbatch
# Arguments go to run_study.py. A quick test of one set:
#   sbatch --time=01:00:00 analysis/obj-b-packets/large/run.sbatch --only base
# 64 cores and 200 GB select a whole 256 GB node in normal. The model needs about 100 GB.
#SBATCH --job-name=packets-large
#SBATCH --partition=normal
#SBATCH --container=el9hw
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --mem=200G
#SBATCH --exclusive
#SBATCH --time=08:00:00
#SBATCH --output=packets-large-%J.out
#SBATCH --error=packets-large-%J.out
exec bash "${SLURM_SUBMIT_DIR:-$PWD}/analysis/obj-b-packets/sooner/job.sh" large 30000 "$@"
