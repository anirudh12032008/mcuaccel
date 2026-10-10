#!/usr/bin/env bash
# Print host and tool versions (source scripts/env.sh first).
v() { local n=$1; shift; printf '%-20s' "$n"; if command -v "$1" >/dev/null 2>&1; then "$@" 2>&1 | head -1; else echo "not found"; fi; }
echo "host                $(uname -srm)"
v yosys yosys -V
v verilator verilator --version
mkdir -p .cache/home
HOME="$PWD/.cache/home" v nextpnr-ecp5 nextpnr-ecp5 --version
v iverilog iverilog -V
v riscv-none-elf-gcc riscv-none-elf-gcc --version
v python python --version
v cocotb python -c 'import cocotb;print(cocotb.__version__)'
v numpy python -c 'import numpy;print(numpy.__version__)'
printf '%-20s' tectonic; (command -v tectonic >/dev/null && tectonic --version || { [ -x /opt/homebrew/bin/tectonic ] && /opt/homebrew/bin/tectonic --version; } || echo "not found") 2>&1 | head -1
v uv uv --version 2>/dev/null || { printf '%-20s' uv; /Library/Frameworks/Python.framework/Versions/3.14/bin/uv --version 2>&1 | head -1; }
