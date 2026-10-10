# SPEC — mcuaccel

Status: v1.1 (Architect 2026-10-10; red-team amendments D15, orchestrator). This file says *what* the project builds and how success
and failure are decided. docs/DESIGN.md says *how*. Change a number here only through a new
entry in docs/DECISIONS.md; never after the measurement it constrains has been taken.

## 1. Purpose

An int8 CNN accelerator, memory-mapped into a small RISC-V SoC, that executes mcugrad's
quantized conv2d, depthwise conv2d and fully connected layers **bit-exactly** (byte-identical to
`mcugrad.quant.sim.run_int8`), together with a mcugrad backend that turns an int8 IR graph into
accelerator command descriptors plus driver C, keeping every other op in mcugrad's software
kernels. The research question: how much end-to-end inference time does a small (~4k LUT4),
memory-bound accelerator buy on a slow microcontroller-class core, and how well does a simple
analytic model, written down before any RTL, predict it?

## 2. System under test

- **SoC** ("S"): PicoRV32 (D1, configuration in §7) behind a corv32-bus adapter, a true
  dual-port on-chip RAM (port A: CPU, port B: accelerator master; the accelerator owns a private
  32-bit port, one word per cycle, one outstanding request), the accelerator's register
  slave, and a simulation console. Single clock. Memory map and RTL: DESIGN §3, §4.
- **Accelerator**: `mcuaccel_top` with parameter `P` (output channels per tile, default 8; the
  datapath has 4·P int8 multipliers). Ablation `P ∈ {1, 2, 4, 8, 16}`.
- **Workloads**: mcugrad's two int8 per-channel models, `mnist_cnn` and `cifar_mobilenet`
  (fused), exactly the graphs that produced
  `third_party/mcugrad/firmware/generated/{mnist_cnn,cifar_mobilenet}/model.c`.
  Layer shapes: DESIGN §2.
- **Execution platform for all cycle numbers**: Verilator simulation of the full SoC
  (`sim/`, D5). The FPGA (ECP5 LFE5U-85F, D4) is used for synthesis results (LUT4, DSP, BRAM,
  Fmax) only. A board run is not required for any claim; if done, it is reported separately.

## 3. Scope

Supported on the accelerator (anything else falls back to mcugrad's C kernels, unchanged):

| op (int8 IR) | condition | mode |
|---|---|---|
| `conv2d` / `conv2d_fused` | `groups == 1`, any KH, KW in 1..15, stride 1..15, pad `PH < KH`, `PW < KW`, any C, any O | conv |
| `conv2d` / `conv2d_fused` | `groups == C == O`, `C % 4 == 0`, input tensor 4-byte aligned, same K/stride/pad limits | dw |
| `matmul` / `matmul_fused` | `[N,K] x [K,M]` | conv with KH=KW=1, H=1, W=N, C=K, O=M |

Additional backend limits (DESIGN §6.4): H, W, OH, OW, C, O ≤ 65535; one command's packed
weights ≤ 512 buffer rows and ≤ 512 output channels (larger layers are split into several
commands); a single tile's reduction (KH·ceil(KW·C/4) rows) ≤ 512 rows, else fall back.

Software fallback (mcugrad C, same code as the baseline): maxpool2d, avgpool2d, pad, transpose,
add, affine, softmax, reshape/flatten (aliases), and any conv/fc outside the table.
Optional backend passes (M4 stretch, not required for any claim): drop `transpose`
NCHW→NHWC when C = 1 (identity on bytes); fold the NHWC→NCHW transpose before an fc into a
permutation of the fc weights (bit-exact, a pure re-indexing of K). **Pre-registered: both passes
are OFF in every scored build (C1, C1b, C4); results with them on are reported separately,
labelled, and never substituted (D15).**

## 4. Numerical specification

The accelerator computes, for every output pixel and output channel, the **unfolded** reference
formula of `mcugrad/quant/int8ops.py` (`conv2d_nhwc_s8`, lines 132-157; `fc_s8`, lines 160-168):

```
acc = bias[o] + sum over in-bounds taps (ky,kx) and input channels ci of (x - x_zp) * w
y   = clamp(requant(acc, out_mult[o], out_shift[o]) + y_zp, act_min, act_max)
```

Padded taps are skipped (they read x_zp and contribute 0). `bias` is the IR's int32 bias
(not mcugrad's codegen-folded `b_<i>`). Depthwise: input channel `ci = o` only. The rounding
definitions, verbatim from `third_party/mcugrad/mcugrad/codegen/kernels/mg_int8.h` lines 11-27:

```
 * SRDHM(a, b)  (SaturatingRoundingDoublingHighMul):
 *     if a == b == INT32_MIN: INT32_MAX
 *     else: (a*b + nudge) / 2^31 with truncating (toward-zero) int64 division,
 *           nudge = 2^30 if a*b >= 0 else 1 - 2^30.
 *     Equivalent closed form: floor((a*b + 2^30) / 2^31)   (round half up, i.e. ties toward +inf).
 *
 * RDBPOT(x, e)  (RoundingDivideByPOT, 0 <= e <= 31):
 *     mask = 2^e - 1; rem = x & mask; thr = (mask >> 1) + (x < 0);
 *     (x >> e) + (rem > thr)              (arithmetic shift)
 *     Equivalent closed form: round-half-away-from-zero of x / 2^e.
 *
 * requant(acc, mult, shift):
 *     ls = max(shift, 0); rs = max(-shift, 0);
 *     a  = acc << ls  (32-bit two's-complement wrap, computed unsigned)
 *     RDBPOT(SRDHM(a, mult), rs)
 *
 * y = clamp(requant(acc, mult[ch], shift[ch]) + y_zp, act_min, act_max); relu is fused via act_min = y_zp.
```

Python mirror: `int8ops.srdhm`, `rdbpot`, `check_shift`, `requant` (lines 39-70) and `_clamp`
(lines 73-74: `lo if v < lo else hi if v > hi else v`, in that order). Precise integer rules:

1. `shift ∈ [-31, 31]` (`check_shift`); the backend rejects anything else, as mcugrad does.
2. Operand widths: `x, w, x_zp, y_zp, act_min, act_max` int8; `(x - x_zp)` 9-bit signed
   (range [-255, 255]); products 17-bit; `bias, mult` int32; accumulator int32.
3. **Accumulator range.** In the spec `acc` is an unbounded integer, but it only enters
   `requant` through `wrap32(acc << ls)`, and `wrap32(wrap32(acc) << ls) = wrap32(acc << ls)`.
   A 32-bit two's-complement accumulator that wraps is therefore exact for *every* input.
   The backend nevertheless enforces the no-wrap bound
   `|bias[o]| + K · 255 · 128 ≤ 2^31 − 1`, `K = KH·KW·C` (conv), `KH·KW` (dw), `K` (fc),
   so the accumulator never wraps (both models satisfy it with K ≤ 1152, margin > 2^20). This
   replaces `fold_bias`'s bound, which only covers the folded form.
4. `requant(...) + y_zp` is evaluated in at least 33 bits before the clamp (Python adds
   unbounded integers; a 32-bit add could wrap where the spec clamps).
5. Output byte = low 8 bits of the clamped value (act_min, act_max are int8, so it fits).

Bit-exactness target: for a layer, the accelerator's output bytes equal
`int8ops.conv2d_nhwc_s8(...)` (or `fc_s8`) on the same arguments; for a network, the final
output bytes of `model_run` equal `sim.run_int8(graph, ...)` and equal the baseline firmware's.

## 5. Definitions

- **Inference time** of a build: the `c1 − c0` interval bench_main measures around `model_run()`
  with `rdcycle` on the SoC CPU (mcugrad's `firmware/common/bench_main.c`; the interval also
  contains two `bench_micros()` calls, whose cost is measured once with an empty `model_run` and
  reported, identical in both builds), including software fallback layers, descriptor patching,
  polling and all driver overhead. Protocol =
  bench_main's: `TESTSET_N = 10` fixed test images (the first 10 of mcugrad's test set),
  one untimed warm-up, `BENCH_RUNS = 3` timed runs over all images (D17: the simulator is
  deterministic, so repeated runs can only demonstrate that; three suffice); reported value =
  median over runs of each run's per-image median (`cycles_median`), with min/max across images
  and any spread across runs reported, not hidden. Runs made with more repetitions (e.g. the
  cifar baseline at 10 × 10) are kept as recorded.
- **Per-layer time**: (a) software layers and whole accelerated chains: an instrumented build
  (`-DMCUACCEL_PROFILE`) reads `rdcycle` around every `op_*` call (instrumentation cost
  reported); (b) each accelerator command: the hardware STAMP word the accelerator writes into
  its descriptor (cycles from descriptor fetch to stamp issue, DESIGN §6.3).
- **Baseline** (D6): mcugrad's own generated `model.c` (unchanged, `mg_int8.c` kernels),
  `riscv-none-elf-gcc -O2 -march=rv32im_zicsr -mabi=ilp32` (zicsr for `rdcycle`), on the same SoC (accelerator present
  and idle), same clock, same images, same bench_main. Label: "PicoRV32 (corv32-bus SoC)".
- **Accelerated build**: the backend's `model.c` (same `model_run` API, same flags) in which
  supported ops are accelerator commands and every other op is the identical mcugrad kernel.
- **Speedup** `S(model, P)` = baseline inference time / accelerated inference time.
- **LUT4, FF, DP16KD, MULT18X18D, Fmax**: from nextpnr-ecp5 `--report` on LFE5U-85F,
  package CABGA381, speed 6, out-of-context, `mcuaccel_top` inside a wrapper that registers
  every port (no logic); LUT4 = `TRELLIS_COMB` (includes carry-chain halves), FF =
  `TRELLIS_FF` minus wrapper FFs. Seed 1 for resources; Fmax median over seeds 1..10.

## 6. Claims (pre-registered)

### C1 — the claim (the brief's)

> **Metric** S(model, P=8) for **both** `mnist_cnn` and `cifar_mobilenet`, measured by
> **procedure** §5 (median `model_run` cycles, 10 images × 3 runs, baseline vs accelerated
> build, software fallback layers and driver overhead included), on **system** §2 (Verilator
> model of the PicoRV32 SoC with `mcuaccel_top #(P=8)`), **will be ≥ 10**. If either network's
> S is < 10 the claim is refuted.

### C1b — stronger pre-registered statement

> S(cifar_mobilenet, P=8) **will be ≥ 100**. If it is < 100, C1b is refuted (C1 may still hold).

Basis of C1b (an estimate, not the pre-registered prediction; that is M1's
`results/m1_predictions.csv`): the accelerator is bound by its single 32-bit RAM port (one word
per cycle); the DESIGN §7 equations give ≈ 1.4·10^5 accelerator cycles for cifar_mobilenet at
P=8, plus the software avgpool (≈ 2048 bounds-checked taps, estimated ≈ 10^5 cycles) and driver. mcugrad needs ≈ 1.6·10^7 instructions
on a Cortex-M0 for this model (third_party/mcugrad/results/emulated.csv); PicoRV32 needs ≥ 3
cycles per instruction (5 per load/store), so the baseline is expected at ≥ 3·10^7 cycles. 100
leaves a margin of only ≈ 1.3× on the accelerated estimate (≈ 2.3·10^5 cycles) and ≥ 2× on the
baseline; the red-team review (D15) noted this, and the threshold stays. MNIST is expected far below 100 because its
maxpools and transposes stay in software (Amdahl); that share is itself a reported result.

### C2 — bit-exactness

(a) 0 mismatching output bytes over **N = 1000** random single-layer commands (seeds
0..999, generator DESIGN §10.2) comparing `mcuaccel_top` against `int8ops.conv2d_nhwc_s8` /
`fc_s8`, covering conv, dw and fc modes, unaligned x/y, random zero points, full int8 weight
range, random `mult` and `shift ∈ [-31, 31]`, random clamps, and randomly stalling bus slaves.
(b) 0 mismatching bytes in every intermediate tensor of both networks for 10 images each
(accelerator-level test: accelerated layers on the RTL, fallback layers through `sim`).
(c) 0 mismatching final output bytes for 100 test images of each network on the full SoC
(Verilator), compared with `sim.run_int8` and with the baseline firmware's output.
Any single mismatching byte refutes C2.

### C3 — resource budget (P = 8, flow of §5)

`mcuaccel_top #(P=8)` uses **≤ 6000 LUT4, ≤ 40 MULT18X18D, ≤ 12 DP16KD**. Exceeding any one
refutes C3. Basis: 4P = 32 multipliers of 9×8 bits (one MULT18X18D each in `synth_ecp5`) plus
one 32×32 requant multiplier (4 MULT18X18D) = 36; P weight-buffer BRAMs + 3 parameter BRAMs =
11; LUT estimate ≈ 4000 (DESIGN §8), i.e. ≈ 1.5-2× a PicoRV32 in its fast configuration, which is
measured in the same flow and reported next to it (corv32 budgets put a comparable RV32IM core
at 2.5-3.5k LUT4). Reported but not claimed: FF (budget ≤ 4000), Fmax (target ≥ 50 MHz), the
SoC's Fmax with and without the accelerator, and wall-time speedup = S × Fmax ratio.

### C4 — prediction accuracy of the M1 model

(a) **Per command.** For every P ∈ {1, 2, 4, 8, 16} and every accelerator command of both
networks, `|predicted − measured| / measured ≤ 20 %`, where measured = the command's STAMP and
predicted = the row of `results/m1_predictions.csv` as committed at tag `m1`, joined on
(model, op, P, split index). At P = 8 no command is split (17 rows per sweep point: 3 mnist +
14 cifar); a measured command with no matching row, or vice versa, counts as a violation.
(b) **Per network, accelerator + driver only.** For P = 8, `|pred − meas| / meas ≤ 10 %` with
meas = median over the 10 images of (the `model_run` interval − the 120-cycle bench window − Σ of
the software fallback ops' rdcycle intervals), all taken per image in ONE accelerated profile
build, and pred = `c4b_pred_cycles` (Σ chains + driver, no window) from the m1 summary. The
software ops are measurements and are reported but not scored (they would make the bound circular).
Any violated row refutes C4; all rows are reported either way, as is the signed error distribution.

**Pre-registration rule.** Tag `m1` (model + predictions) precedes in git history every commit
that adds a datapath or sequencer to `rtl/accel/` (the walking-skeleton stub, a register file
that sets DONE without computing, is exempt). The model is not changed to match the RTL: model
fixes after `m1` may be made but their outputs are reported as "post-hoc" beside the original
predictions, which remain the scored ones. **The RTL is not tuned to match the model either**:
the RTL is scored as it stood at its first full STAMP measurement (commit recorded in
results/m6_pred_vs_meas.csv); every later RTL change that alters any STAMP is logged in
docs/DECISIONS.md with before/after values and the scored numbers are the first measurement's.
Unit tests check the RTL's *functional* request stream (addresses, tags, order) against the
backend executor, not its cycle count. Baseline (software) cycles are measurements, not
predictions, and may be taken at any time. The `m1` commit hash and the sha256 of
`results/m1_predictions.csv` are written into the paper and FINAL_REPORT.md; because this
repository has no remote, publishing them externally is a manual step listed in HARDWARE_TODO.md.

**Known model biases written down before m1** (DESIGN §7.4, also listed in the m1 summary): the
+1 PSETUP cycle per pixel is over-predicted where a layer is requant or credit bound (mnist op_1
+3.4 %, cifar op_2 +3.0 % at P = 8; up to +3 % on cifar op_0/4/6 at P = 16; others < 0.5 %); FLUSH
over-predicted by 2 + W_last cycles per command; driver under-predicted by ≈ 40 cycles per network;
`*_WAIT` phases exact only if fetch idle is combinational as specified.

### C5 — where the speedup stops scaling (descriptive, with one falsifiable part)

The P sweep {1, 2, 4, 8, 16} is reported as the curve of Σ STAMP (accelerator cycles only) and of
accelerated `model_run` cycles per network, with the per-layer regime the model assigns (port
bound, requant bound, depthwise fixed 4 MAC/cycle, WLOAD bound). The falsifiable part is C4(a)
at every P: the model must predict every command within 20 % at every width, so it must get the
flattening right. Descriptive only: the saturation width P\* = smallest P whose Σ STAMP is within
10 % of P = 16 ("16" means not reached on this grid), predicted vs measured. **Mechanism stated in
advance:** in this design the port term still falls as 1/P in conv mode (x is re-fetched per
tile), so the flattening comes from the depthwise layers (fixed 4 MAC/cycle, ≈ 45 % of cifar's
cycles at P = 16 by the model), the single requantizer (1 output/cycle) and WLOAD (≈ constant in
P), not from the 32-bit port saturating; the paper says so.

## 7. Fixed configuration

PicoRV32 parameters (D12): `ENABLE_COUNTERS=1, ENABLE_COUNTERS64=1, ENABLE_REGS_16_31=1,
ENABLE_REGS_DUALPORT=1, LATCHED_MEM_RDATA=0, TWO_STAGE_SHIFT=1, BARREL_SHIFTER=1,
TWO_CYCLE_COMPARE=0, TWO_CYCLE_ALU=0, COMPRESSED_ISA=0, CATCH_MISALIGN=1, CATCH_ILLINSN=1,
ENABLE_PCPI=0, ENABLE_MUL=0, ENABLE_FAST_MUL=1, ENABLE_DIV=1, ENABLE_IRQ=0, ENABLE_TRACE=0,
PROGADDR_RESET=32'h8000_0000, STACKADDR=32'h8000_0000 + RAM_BYTES`. Compiler and flags: D6.
RAM: 1 MiB in Verilator, 256 KiB for FPGA synthesis of the SoC. Accelerator: P = 8 unless an
ablation says otherwise; weight buffer 512 rows × 4P bytes; parameter buffer 512 channels.

## 8. Relation to prior art

docs/PRIOR_ART.md §6 shows that a dedicated int8 accelerator beating a scalar in-order RISC-V
core by well over 10× is established (Gemmini: 127× on MobileNetV2 vs in-order Rocket; CFU
Playground: 55× on the dominant 1×1 conv; VTA on edge FPGAs: 2-6×). C1 therefore reproduces a
known effect on a new, much smaller system (one private 32-bit port of a dual-port BRAM, ~4k LUT4, PicoRV32) and
is kept because the brief asks for it. What this project adds, and what the paper is organised
around: (i) the speedup and per-command cycle counts are predicted by an analytic port-bound
model committed before the RTL exists and scored against measurement with a tolerance fixed in
advance (C4, C5); earlier analytic models (Zhang 2015, Alwani 2016) were fitted alongside their
designs. (ii) Bit-exactness is asserted at the whole-network level against a specific compiler's
integer simulator, with the non-accelerated layers running the same software as the baseline
(C2). (iii) The MAC-width sweep P ∈ {1..16} with one private 32-bit port gives the scaling curve and
identifies the depthwise-, requant- and weight-load-bound regimes for a microcontroller-class
memory system (C5). (iv) The Amdahl
share of pooling and layout ops is measured, not assumed. Negative results on any of these are
reported as such.

## 9. Test plan summary

Details in DESIGN §10.
- Unit (cocotb, Verilator): every `mcuaccel_*` and `soc_*` module; requant against
  `int8ops.requant` (directed edge cases + 10^5 random); bus-protocol checker on every port.
- Accelerator level: 1000 random layers (C2a), whole networks layer by layer (C2b),
  descriptor chains, error responses, random slave stalls.
- Backend: pytest of packing, descriptor fields and splitting against the Python reference
  executor of the descriptor semantics; bound checks; fallback selection.
- Full system (Verilator C++ harness): both networks, baseline and accelerated builds,
  C2c, C1, C4, C5 measurements; per-layer profile build.
- Synthesis: Yosys + nextpnr for C3, lint clean (`make lint`) at every commit.

## 10. Out of scope

Training, quantization changes (mcugrad's scheme is the spec), pooling/add/softmax in hardware,
grouped conv other than depthwise multiplier 1, channel multiplier > 1, int16/int4, sparsity,
caches, DMA beyond the accelerator's own master port, interrupts (the driver polls), multiple
in-flight commands from different masters, an FPGA board run (HARDWARE_TODO if attempted),
corv32_core integration (D1: drop-in later), ASIC/Tiny Tapeout (stretch only).
