# Environment

Project-local toolchain (see DECISIONS D2). Regenerate the block below with `scripts/print_env.sh`.

```
host: macOS 27.0 arm64
yosys                Yosys 0.69+187 (git sha1 2f08661dd, Release, Clang /usr/local/osxcross/target/bin/aarch64-apple-darwin25.5-clang++ 21.1.8)
sby                  SBY v0.69
verilator            Verilator 5.053 devel rev v5.052-119-g014c9820d (mod)
nextpnr-ecp5         "nextpnr-ecp5" -- Next Generation Place and Route (Version nextpnr-0.11.1-47-ge2fe86b3)
riscv-none-elf-gcc   riscv-none-elf-gcc (xPack GNU RISC-V Embedded GCC arm64) 15.2.0
spike                Spike RISC-V ISA Simulator 1.1.1-dev
bitwuzla             0.9.1
boolector            3.2.4
yices-smt2           Yices 2.7.0
z3                   Z3 version 4.15.5 - 64 bit
python               Python 3.12.13
tectonic             Tectonic 0.17.0
riscof               RISC-V Architectural Test Framework., version 1.25.3
cocotb               2.1.0
```

## Missing system packages

- None required. Spike (riscv-isa-sim 609dbe0) and dtc are built from pinned sources by
  scripts/bootstrap.sh; on macOS with CommandLineTools clang/clang++ called directly.
- No FPGA board is attached: on-hardware measurements are listed in HARDWARE_TODO.md.
