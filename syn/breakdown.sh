#!/usr/bin/env bash
# Per-module resource breakdown of mcuaccel_top_wrap (C3 analysis). For each P: Yosys synth_ecp5
# -noflatten (per-module cells) and the normal flattened synth_ecp5 (same as synth.sh), both
# `stat -json`; then syn/breakdown.py joins them with the post-place rows in results/syn/accel_P*.jsonl
# and writes results/syn/accel_breakdown.json. No nextpnr here.
# Usage: syn/breakdown.sh [P ...]   (default 1 2 4 8 16). Needs tools on PATH (source scripts/env.sh).
set -euo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"
cd "$R"
PS="${*:-1 2 4 8 16}"
TOP=mcuaccel_top_wrap
SRC="$(find rtl/accel -name '*.sv' | sort | tr '\n' ' ') syn/wrap/mcuaccel_top_wrap.sv"
for P in $PS; do
  B="$R/build/syn/breakdown_P$P"
  mkdir -p "$B"
  for MODE in hier flat; do
    FL=""; [ "$MODE" = hier ] && FL="-noflatten"
    yosys -q -l "$B/$MODE.log" -p "read_verilog -sv $SRC; chparam -set P $P $TOP; hierarchy -top $TOP; \
      synth_ecp5 $FL -top $TOP; tee -q -o $B/$MODE.json stat -json"
  done
  echo "P=$P done"
done
python3 syn/breakdown.py "$R/results/syn/accel_breakdown.json" $PS
