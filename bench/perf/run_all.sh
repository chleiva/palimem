#!/usr/bin/env bash
# Full benchmark pass (docs/PERFORMANCE.md section 8), strictly sequential: nothing else may load the machine while this runs.
# Takes about 65 minutes on a laptop. Run from the repository root with a venv that has the package installed (pip install -e ".[dev]").
set -u
cd "$(dirname "$0")/../.."
export PYTHONPATH=src:.
PY=${PY:-.venv/bin/python}
R=bench/perf/results
log() { echo "[$(date +%H:%M:%S)] $*"; }
log "start"
# small and medium scale, default population scaling (people = reports / 4)
$PY -m bench.perf run w1 --reports 300 --r 3 --max-seconds 900 && log "w1 300 done"
$PY -m bench.perf run w1 --reports 1000 --r 3 --max-seconds 1800 && log "w1 1000 done"
$PY -m bench.perf run w2 --reports 300 --r 3 --max-seconds 900 && log "w2 300 done"
$PY -m bench.perf run w2 --reports 1000 --r 3 --max-seconds 1800 && log "w2 1000 done"
$PY -m bench.perf run w3 --reports 300 --r 3 --max-seconds 900 && log "w3 300 done"
$PY -m bench.perf run w3 --reports 1000 --r 3 --max-seconds 1800 && log "w3 1000 done"
# entity count versus log length, controlled
$PY -m bench.perf run w1 --reports 1000 --persons 50 --r 3 --max-seconds 900 && log "w1 1000 persons=50 done"
$PY -m bench.perf run w1 --reports 300 --persons 250 --r 3 --max-seconds 900 && log "w1 300 persons=250 done"
# recovery, crossover, profile
mkdir -p .bench
$PY -m bench.perf recovery --preload 300 --extra 300 --workdir .bench && log "recovery 300 done"
$PY -m bench.perf recovery --preload 1000 --extra 300 --workdir .bench && log "recovery 1000 done"
$PY -m bench.perf crossover --reports 300 --r 3 --workdir .bench --max-seconds 900 && log "crossover 300 done"
$PY -m bench.perf crossover --reports 1000 --r 3 --workdir .bench --max-seconds 1800 && log "crossover 1000 done"
$PY -m bench.perf profile w1 --reports 1000 --window 100 && log "profile 1000 done"
$PY -m bench.perf profile w1 --reports 1000 --window 100 --persons 50 --out $R/profile-w1-1000-p50.json && log "profile 1000 persons=50 done"
# large scale: default population scaling, 20 minute budget, stops early by design (PERFORMANCE.md section 5)
$PY -m bench.perf run w1 --reports 10000 --r 3 --max-seconds 1200 --out $R/workload-w1-large-budget.json && log "w1 large (budget) done"
# supplementary: fixed population (250 people), long log, 25 minute budget: isolates log length from entity count
$PY -m bench.perf run w1 --reports 10000 --persons 250 --r 3 --max-seconds 1500 --out $R/workload-w1-fixedpop-250.json && log "w1 fixed population done"
# heap evidence (tracemalloc is slow: small size)
$PY -m bench.perf heap w1 --reports 400 --persons 50 && log "heap done"
log "all complete"
