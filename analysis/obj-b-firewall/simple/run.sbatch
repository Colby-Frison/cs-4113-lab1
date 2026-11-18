#!/usr/bin/env bash
# Simple model with a firewall on every router: 10 routers, 5000 packets per run. Submit from the repository root:
#   sbatch analysis/obj-b-firewall/simple/run.sbatch
# Arguments go to run_study.py. A quick test of one set:
#   sbatch --time=00:20:00 analysis/obj-b-firewall/simple/run.sbatch --only base --seeds 1
#SBATCH --job-name=firewall-simple
#SBATCH --partition=normal
#SBATCH --container=el9hw
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=32G
#SBATCH --time=03:00:00
#SBATCH --output=firewall-simple-%J.out
#SBATCH --error=firewall-simple-%J.out
exec bash "${SLURM_SUBMIT_DIR:-$PWD}/analysis/obj-b-packets/sooner/job.sh" obj-b-firewall/simple 1000 "$@"
