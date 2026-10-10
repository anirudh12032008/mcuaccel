# Decisions

Format: options considered, choice, reason. Newest at the bottom.

## D1 Host CPU for the SoC (2026-10-10)
corv32 at commit e1d0bc0 ships only `corv32_sc`, a walking-skeleton core executing LUI, ADDI, SW,
JAL (its PLAN has M1 "single-cycle RV32I" open). Software inference cannot run on it.
Options: (a) wait for or implement the corv32 core here; (b) use PicoRV32 (ISC licence, already
vendored by corv32 as a baseline) as a stand-in CPU behind corv32's documented bus protocol;
(c) write a small custom RV32IM core.
Choice: (b). Reason: the claim compares accelerator vs software *on the same core*; it holds for
any core. PicoRV32 is small, well tested, synthesizes with Yosys, and its single memory port adapts
to corv32's req/gnt/rvalid bus (corv32 DESIGN section 3) in a few lines. The accelerator's slave and
master ports speak corv32's bus, so `corv32_core` drops in once it exists. Baseline numbers are
labelled "PicoRV32 (corv32-bus SoC)". (a) is out of scope; (c) would spend the budget on a CPU.

## D2 How the dependencies are brought in (2026-10-10)
mcugrad: git submodule at e9f6283 (https://github.com/anirudh12032008/mcugrad.git is reachable).
corv32: it has no remote, so a vendored subset (rtl/, docs/, sw/common, sw/tools, LICENSE) at
e1d0bc0, recorded in third_party/corv32/VENDORED.md. PicoRV32: `picorv32.v` + COPYING vendored,
upstream commit recorded in third_party/picorv32/VENDORED.md.
Reason: submodule where a stable URL exists; vendored copy where it does not; both pinned.

## D3 Toolchain (2026-10-10)
Options: system installs; project-local downloads; reuse the sibling corv32 checkout's `tools/`.
Choice: `scripts/bootstrap.sh` reuses `../corv32/tools` and `../corv32/.venv` Python when present
(identical pinned versions: OSS CAD Suite 2026-10-03, xPack GCC 15.2.0-1, Python 3.12) and
otherwise downloads the same pins into `tools/`. Nothing is installed system-wide. Tectonic
(LaTeX) is used from Homebrew if present and recorded in docs/ENVIRONMENT.md.

## D4 FPGA target for budgets and synthesis (2026-10-10)
Choice: Lattice ECP5 LFE5U-85F (ULX3S), Yosys `synth_ecp5` + nextpnr-ecp5, the same flow corv32
uses, so accelerator and CPU numbers come from one tool chain. Budgets are written in docs/SPEC.md
before RTL exists. iCE40/Tiny Tapeout (sky130 via LibreLane) is the stretch variant only.

## D5 Simulation split (2026-10-10)
cocotb (Verilator backend) for unit tests and accelerator-level bit-exact tests against mcugrad's
simulator; a Verilator C++ harness (`sim/`) for whole-network full-system runs, because a
cifar_mobilenet software inference on PicoRV32 is tens of millions of cycles, impractical with a
per-cycle Python callback.

## D6 Baseline definition (2026-10-10)
Software baseline = the C that mcugrad already emits (`mcugrad.codegen`, kernels `mg_int8.c`),
compiled with `riscv-none-elf-gcc -O2 -march=rv32im -mabi=ilp32`, running on the same SoC CPU at the
same clock, cycles read from `rdcycle`. The accelerated build uses the same compiler flags, the same
model, the same test images, and the same CPU for the layers that fall back to software.
