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

## D7 Smoke-test layout and empty-suite handling (2026-10-10)
Toolchain smoke tests live in `tb/unit/test_toolchain.py` against a trivial `rtl/soc/soc_smoke_counter.sv`
(kept in rtl/ so lint and synthesis flows see real input). `make test` tolerates pytest exit code 5
(no tests collected) so the target stays green on a bare checkout; `make lint` prints "no RTL yet" when
rtl/ is empty. Reason: the targets must be CI-safe at every stage of the build-up.

## D8 Architect: command interface = chained descriptors in RAM (2026-10-10)
Options: (a) ~24 accelerator registers written by the CPU per layer; (b) one 18-word descriptor
per command in RAM, fetched by the accelerator's own master port, chained by a NEXT pointer.
Choice: (b). Reason: a PicoRV32 store costs ~5 cycles plus constant building, so (a) is
~250 CPU cycles per layer; (b) costs 2 stores + polling per *chain* (cifar_mobilenet's 13
consecutive accelerated layers are one chain) and 17 port cycles per command. Descriptors are
backend-emitted data; only input/output-dependent fields are patched per call. The register
file shrinks to 8 registers. No IRQ (the driver polls; model_run is synchronous).

## D9 Architect: datapath = output-stationary 4 lanes x P, row-run reduction (2026-10-10)
Options for C % 4 != 0 (C = 1, 3): backend channel padding (CPU re-layout pass, extra RAM);
per-tap lane masking of partial words (3 of 4 lanes idle for C = 1, unaligned pixels);
**row runs**: reduce over the contiguous NHWC bytes (kx, c) of one kernel row through a
4-byte aligner with lane masks for borders. Choice: row runs. Reason: one mechanism for any C,
C = 1 and C = 3 cost no more than C = 4, border handling = clip to quads + lane mask, no
CPU work. Dw: dedicated 4-lane mode on output group 0 (4 MACs/cycle, honest 1/P utilisation).
Rejected for building, kept in the M1 model: weight-stationary/line-buffer "lb" (x reuse
across taps; helps dw and C < 4 only, costs KH line buffers) and accumulator-SRAM "acc"
(x once per pixel; same cycles on a zero-wait 1-word/cycle port, fewer bus words).

## D10 Architect: unfolded arithmetic, bias added at requant, 32-bit wrap (2026-10-10)
The accelerator computes int8ops' unfolded form, acc starting at 0 and bias added in requant
S1. A wrapping int32 accumulator equals the spec for all inputs because the spec only uses
wrap32(acc << ls); the backend still enforces |bias| + K*255*128 <= 2^31-1. Requant + y_zp is
33-bit before the clamp. Rejected: mcugrad's folded bias (needs per-pixel padded-tap
corrections in hardware).

## D11 Architect: buffers, tiling, P default (2026-10-10)
Weight buffer 512 rows x 4P bytes (P DP16KD, 16 KiB at P = 8: exactly cifar op_12's
128x128 1x1 at P = 8); parameter buffer 512 channels (bias, mult, shift; 3 DP16KD). Layers
exceeding a buffer are split by the backend into commands over output (conv) or channel (dw)
slabs; a single tile needing > 512 rows falls back to software. Requant: one pipelined unit
(32x32, 6 stages, 1/cycle) with a 2-deep result FIFO and 2 credits. P = 8 default (36
MULT18X18D, ~3.8k LUT4); ablation P in {1, 2, 4, 8, 16}; NACC = max(P, 4) so dw works for all P.

## D12 Architect: SoC and baseline CPU configuration (2026-10-10)
PicoRV32 "performance" config: FAST_MUL, DIV, BARREL_SHIFTER, COUNTERS(64) on, COMPRESSED off,
dual-port regfile, IRQ off (full list SPEC §7); reset 0x8000_0000, no boot ROM, RAM preloaded.
RAM 1 MiB (Verilator) / 256 KiB (FPGA synthesis), true dual port (CPU port A, accelerator
port B, no arbiter). Accelerator regs 0x3000_0000, sim console 0x1000_0000 (putchar, tohost,
64-bit cycle counter). The adapter raises req combinationally so a zero-wait RAM answers
in the next cycle, PicoRV32's reference memory timing.

## D13 Architect: claims and pre-registration (2026-10-10)
C1: S(cifar_mobilenet, P=8) >= 100 (estimate ~1.4e5 accel cycles vs >= 3e7 baseline cycles,
factor >= 2 margin each way). Secondary: bit-exactness (1000 random layers, both networks),
resources (<= 6000 LUT4, <= 40 MULT18X18D, <= 12 DP16KD), M1 accuracy (<= 20 % per command,
<= 10 % per network), mnist >= 10x. Predictions are scored from `git show m1:...` only.

## D14 Orchestrator: claim thresholds and framing (2026-10-10)
Options: (a) C1 = cifar_mobilenet >= 100x as the Architect proposed; (b) C1 = the brief's
>= 10x on both networks, with the Architect's 100x kept as a stronger pre-registered statement;
(c) drop the speedup claim since prior art settles >10x (docs/PRIOR_ART.md section 6).
Choice: (b). Reason: the brief fixes the claim at 10x and it is the user's project; prior art
settling 10x is addressed by framing (SPEC section 8): the contribution is the pre-registered
model (C4), whole-network bit-exactness with fallback layers (C2), the MAC-width saturation curve
(C5) and measured Amdahl shares, not the 10x itself. The 100x statement (C1b) is kept because it
is the number the design equations imply and a miss would be informative.

## D15 Orchestrator: red-team amendments to SPEC/DESIGN before any RTL (2026-10-10)
The plan red-team (Opus, read-only) found 1 blocker, 8 major, 12 minor issues. Resolutions:
test sets regenerated at 10/100 images, 4-byte aligned (blocker); C4 split into per-command at
every P and per-network on accelerator+driver only, RTL scored at its first full measurement with
later timing changes logged (closes the "conformance, not prediction" and circularity findings);
C5 made descriptive except for C4-at-every-P, mechanism (dw/requant/WLOAD bound, not port
saturation) stated in advance; "single-port BRAM" wording corrected to a private port of a
dual-port RAM; optional backend passes pre-registered OFF for scored builds; driver written before
m1; memory barriers and uintptr_t in the driver; guard rule made a link-time property; abort
recovery enumerated; signed compares in requant S5; STAMP cross-checked against CYCLES and VCD;
fallback-code identity checked by objdump hash; max_cycles default raised; no parallel-image
fallback; `make reproduce` must include experiments. Rejected: a denser P grid (DSP budget; P*
on the doubling grid is reported descriptively instead); external timestamping of m1 (no remote;
hash recorded, manual step in HARDWARE_TODO.md).
