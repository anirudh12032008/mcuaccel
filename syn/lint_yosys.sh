#!/usr/bin/env bash
# Yosys front-end check: all given RTL files must parse and elaborate. Usage: lint_yosys.sh file.sv...
set -euo pipefail
[ $# -eq 0 ] && { echo "no RTL yet"; exit 0; }
yosys -q -p "read_verilog -sv $*; hierarchy -check -auto-top"
