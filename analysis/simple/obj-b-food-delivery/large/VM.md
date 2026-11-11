# Run the large study on dos8

The full study has completed and its results are imported locally. These commands
remain the reproduction/reference workflow. See [the measured analysis](README.md#measured-results-and-interpretation).

From `~/food-delivery-lab` after extracting the updated source bundle:

Include the sibling `analysis/simple/obj-b-food-delivery/study_tools/` directory
in the bundle; medium and large reporting and archiving share these helpers.
Code changes invalidate collection fingerprints: use a fresh output for new
collections, while existing results can still be reported and audited.

```bash
source implementation/obj-b.food-delivery/.venv/bin/activate
cmake -S implementation/obj-b.food-delivery/cpp \
  -B implementation/obj-b.food-delivery/cpp/build -DCMAKE_BUILD_TYPE=Release
cmake --build implementation/obj-b.food-delivery/cpp/build -j 2
python -m unittest discover -s implementation/obj-b.food-delivery/tests -v
```

This builds both `food_server` (existing serial worker) and `food_host` (new shared
host). Python dependencies remain those already installed for the medium app and
plots: grpcio, grpcio-tools, protobuf, numpy, pandas, matplotlib, pillow.

## First run: 200-queue baseline smoke test

Use a fresh output directory. This runs 5,000 orders with seed 1, generates six
main plots and accessory outputs, and archives the raw run:

```bash
python analysis/simple/obj-b-food-delivery/large/run_study.py \
  --smoke --output "$HOME/food-large-smoke" \
  --collect --report --archive
python analysis/simple/obj-b-food-delivery/large/validate_study.py \
  --output "$HOME/food-large-smoke"
```

Check `DONE` output for zero incomplete orders and low P95 injection lag. Drops
are valid modeled outcomes, not missing orders. If this directory already holds
a failed run, use a new output name instead of overwriting it. Smoke results are
not a substitute for the full 100,000-order runs.

For a seven-scenario pilot, use another fresh output directory:

```bash
python analysis/simple/obj-b-food-delivery/large/run_study.py \
  --output "$HOME/food-large-seven-pilot" --count 10000 --seeds 1 \
  --collect --report --archive
```

## Full study in tmux

Start a persistent shell:

```bash
tmux new -s food-large
```

Inside tmux:

```bash
cd ~/food-delivery-lab
source implementation/obj-b.food-delivery/.venv/bin/activate
export MPLBACKEND=Agg
export MPLCONFIGDIR="$HOME/.cache/food-delivery-matplotlib"
mkdir -p "$MPLCONFIGDIR"
set -o pipefail
python -u analysis/simple/obj-b-food-delivery/large/run_study.py \
  --collect --report --archive 2>&1 | tee "$HOME/food-large-study.log"
```

This collects **21 runs / 2.1 million orders**, sequentially, using the canonical
`study.json`, then writes all 42 main plots and archives the raw runs. Do not also
launch a pilot while this is collecting. `RUN`, `DONE`, `PLOTS`, and `ARCHIVED`
lines show progress; a run can take over 100 seconds between its RUN and DONE.

Detach with **Ctrl-b, then d**. Reconnect with `tmux attach -t food-large`.
Detach preserves processes; shutting down or expiring the VM does not. From
another SSH session, `tail -f ~/food-large-study.log`, `free -h`, and `top` help
monitor it.

After success:

```bash
python analysis/simple/obj-b-food-delivery/large/validate_study.py
cat analysis/simple/obj-b-food-delivery/large/results/summary.md
```

`LAGGED` rows indicate a P95 injection delay over 10 ms. Preserve them and inspect
the diagnostics before drawing capacity conclusions. For a differently paced
experiment, create a separate configuration/output rather than mixing runs.

## Resume or replot

The same full command reuses completed runs if collection hashes and configurations
match. It reads both directories and archives. Changed binaries/source/configuration
are rejected so different implementations cannot silently mix in one experiment.
A partial/failed run is not resumable; preserve it and use a fresh output for a
new experiment (or relocate the failed run outside this experiment before retrying).

Rebuild plots without starting services:

```bash
python analysis/simple/obj-b-food-delivery/large/run_study.py --report
```

For a pilot directory, supply the same `--output` to report or audit. Its saved
experiment specification supplies count and seeds, so overrides are unnecessary.

## Bring results back to the Mac

Run this in a **Mac Terminal**, not inside SSH. The destination is a separate
folder, so it does not overwrite local study files:

```bash
mkdir -p ~/Downloads/food-large-vm
scp -r -i ~/.ssh/id_ed25519_dos \
  student@dos8.cs.ourcloud.ou.edu:~/food-delivery-lab/analysis/simple/obj-b-food-delivery/large/results \
  ~/Downloads/food-large-vm/
```

`results/` includes the main and accessory plots, summary, CSVs, and audit output.
Copy `large/runs/` separately if you want the archived raw evidence on the Mac too.
