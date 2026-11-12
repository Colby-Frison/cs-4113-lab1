#!/usr/bin/env bash
# Simple model: 10 routers, 5000 packets per run. Submit from the repository root:
#   sbatch analysis/obj-b-packets/simple/run.sbatch
# Arguments go to run_study.py. A quick test of one set:
#   sbatch --time=00:20:00 analysis/obj-b-packets/simple/run.sbatch --only base --seeds 1
#SBATCH --job-name=packets-simple
#SBATCH --partition=normal
#SBATCH --container=el9hw
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=32G
#SBATCH --time=03:00:00
#SBATCH --output=packets-simple-%J.out
#SBATCH --error=packets-simple-%J.out
exec bash "${SLURM_SUBMIT_DIR:-$PWD}/analysis/obj-b-packets/sooner/job.sh" simple 1000 "$@"
