#!/usr/bin/env bash
# make reproduce: baselines -> M3 campaign -> M5/M6 SoC runs -> synthesis sweep -> macros -> paper -> README/FINAL_REPORT.
# REPRODUCE_QUICK=1: tiny image/seed counts as a smoke run. The scripts write into results/, so quick mode
# snapshots results/ + paper/ + README first and restores them on exit (the real results are never clobbered).
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh
PY=python
if [ -n "${REPRODUCE_QUICK:-}" ]; then
  B=build/reproduce_quick_backup; mkdir -p "$B"
  cp -R results "$B/results.$$"; cp -R paper "$B/paper.$$"; cp README.md "$B/README.$$"
  restore() { cp -R "$B/results.$$/." results/; cp -R "$B/paper.$$/." paper/; cp "$B/README.$$" README.md
              echo "quick mode: results/ paper/ README restored (copies kept in $B)"; }
  trap restore EXIT
  IMG="--images 1 --runs 1"; BASE="$IMG --no-profile"; M3="--quick"; SOCP="--P 8"; PROF="--profile --prof-images 1 --base-images 1"; BX=2
else
  BASE=""; M3=""; SOCP="--P 1 --P 2 --P 4 --P 8 --P 16"; PROF="--profile"; BX=100; IMG=""
fi
step() { echo; echo "=== $* ($(date +%T))"; }

step "build SoC simulator"; make sim
step "baselines (D6/D19)"; $PY experiments/run_baseline.py $BASE
step "driver overhead"; $PY experiments/run_drv_timing.py
step "M3 campaign (C2a, C2b, chains, errors)"; $PY experiments/run_m3.py $M3
step "M5 timing per P (C1, C1b, C5)"; $PY experiments/run_soc.py $SOCP $IMG
step "M5/M6 profile + C4b at P=8"; $PY experiments/run_soc.py --P 8 $PROF
step "M5 C2c bit-exactness on the SoC"; $PY experiments/run_soc.py --P 8 --bitexact $BX
# C4a (results/m6_pred_vs_meas.csv) has no generator yet; it runs here once one is added.
if [ -f experiments/run_pred_vs_meas.py ]; then step "C4a pred vs meas"; $PY experiments/run_pred_vs_meas.py; fi
step "synthesis sweep"
if [ -n "${REPRODUCE_QUICK:-}" ]; then bash syn/synth.sh accel 8 1; else bash experiments/run_syn_sweep.sh 3; fi
step "macros"; $PY experiments/make_macros.py
step "paper"; make paper
step "README tables + FINAL_REPORT"; $PY experiments/make_readme_tables.py; $PY experiments/make_final_report.py
echo; echo "reproduce done"
