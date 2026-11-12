#!/usr/bin/env bash
# Very large model with a firewall on every router: 3400 decomposed routers, 10200 queues, 20740 processes, 100000 packets
# per run. Submit from the repository root:
#   sbatch analysis/obj-b-firewall/very-large/run.sbatch
# Arguments go to run_study.py. A quick test of one set:
#   sbatch --time=02:00:00 analysis/obj-b-firewall/very-large/run.sbatch --only base
# A largemem node (at least 56 cores, about 2 TB) fits about 1 TB of processes, so the job takes all of it.
#SBATCH --job-name=firewall-very-large
#SBATCH --partition=largemem
#SBATCH --container=el9hw
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=0
#SBATCH --exclusive
#SBATCH --time=24:00:00
#SBATCH --output=firewall-very-large-%J.out
#SBATCH --error=firewall-very-large-%J.out
exec bash "${SLURM_SUBMIT_DIR:-$PWD}/analysis/obj-b-packets/sooner/job.sh" obj-b-firewall/very-large 300000 "$@"
