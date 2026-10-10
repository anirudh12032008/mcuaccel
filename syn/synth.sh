#!/usr/bin/env bash
# Yosys synth_ecp5 + nextpnr-ecp5 (out-of-context, LFE5U-85F) -> one JSONL row per run in
# results/syn/<design>[_P<P>].jsonl (DESIGN 10.5).
# Usage: syn/synth.sh DESIGN [P] [SEED]   DESIGN in {accel, picorv32, soc, soc_noaccel}
#   accel: mcuaccel_top_wrap with chparam P; soc: soc_top (RAM 256 KiB, accelerator at P);
#   soc_noaccel: soc_top built with -DMCUACCEL_NONE; picorv32: picorv32_wrap (SPEC 7 config).
# Needs the tools on PATH (source scripts/env.sh). Not parallel: Yosys+ABC is heavy.
set -euo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"
DESIGN="${1:?usage: synth.sh DESIGN [P] [SEED]}"
P="${2:-8}"
SEED="${3:-1}"
FREQ=50
cd "$R"
mkdir -p "$R/.cache/home" "$R/results/syn"
export HOME="$R/.cache/home"

ACC="$(find rtl/accel -name '*.sv' | sort | tr '\n' ' ')"
SOC="$(find rtl/soc -name '*.sv' | sort | tr '\n' ' ')"
PICO=third_party/picorv32/picorv32.v
DEFS=""; CH=""; PROW="$P"; TAG="${DESIGN}_P${P}"
case "$DESIGN" in
  accel)       TOP=mcuaccel_top_wrap; SRC="$ACC syn/wrap/mcuaccel_top_wrap.sv"; CH="chparam -set P $P $TOP;" ;;
  picorv32)    TOP=picorv32_wrap; SRC="$PICO syn/wrap/picorv32_wrap.sv"; PROW=null; TAG="$DESIGN" ;;
  soc)         TOP=soc_top; SRC="$PICO $ACC $SOC"; CH="chparam -set RAM_BYTES 262144 -set P $P $TOP;" ;;
  soc_noaccel) TOP=soc_top; SRC="$PICO $ACC $SOC"; DEFS="-DMCUACCEL_NONE"; PROW=null; TAG="$DESIGN"
               CH="chparam -set RAM_BYTES 262144 $TOP;" ;;
  *) echo "unknown DESIGN '$DESIGN' (accel|picorv32|soc|soc_noaccel)" >&2; exit 2 ;;
esac

B="$R/build/syn/${TAG}_s${SEED}"
mkdir -p "$B"
rm -f "$B"/*
T0=$(date +%s)
yosys -q -l "$B/yosys.log" -p "read_verilog -sv $DEFS $SRC; $CH hierarchy -top $TOP; synth_ecp5 -top $TOP -json $B/design.json; tee -o $B/stat.txt stat"
nextpnr-ecp5 --85k --package CABGA381 --speed 6 --out-of-context --freq $FREQ \
  --timing-allow-fail --quiet --seed "$SEED" --json "$B/design.json" --report "$B/report.json" -l "$B/nextpnr.log" >/dev/null
WALL=$(( $(date +%s) - T0 ))
python3 syn/extract.py "$B/report.json" "$B/nextpnr.log" "$B/stat.txt" \
  "$R/results/syn/${TAG}.jsonl" --design "$DESIGN" --p "$PROW" --seed "$SEED" --freq "$FREQ" \
  --wall "$WALL" --json "$B/design.json" --top "$TOP"
