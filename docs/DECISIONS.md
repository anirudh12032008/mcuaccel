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
compiled with `riscv-none-elf-gcc -O2 -march=rv32im_zicsr -mabi=ilp32` (zicsr for `rdcycle`), running on the same SoC CPU at the
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

## D16 Orchestrator: slow tests out of the default `make test` (2026-10-10)
The mcugrad int8 graph build (pure-Python calibration over 512 images) took 2275 s for mnist on
this loaded machine and much longer for cifar. Options: run it in `make test` every time; cache
the JSON under build/ and still run the reproduction test; mark `slow` and run via `make test-slow`.
Choice: `slow` marker, excluded from `make test`, included in `make test-slow` and in the Step 5
clean-clone verification. Reason: a 1-2 h default test run would stop every agent from testing.

## D17 Orchestrator: timing protocol 10 images x 3 runs (2026-10-10)
SPEC v1 said 10 x 10. The Verilator model is deterministic (measured: cifar cycles_min/max differ
only across images), so extra runs add wall time and no information; under the shared host load
(~400) a 10 x 10 cifar run is many hours. Options: keep 10 x 10; 10 x 3; 10 x 1.
Choice: 10 x 3 (three runs still demonstrate determinism), decided before any accelerated
measurement exists. A 10 x 10 baseline already running is kept if it completes.

## D18 Orchestrator: generated-code conventions of the backend emitter (2026-10-10)
Decided while implementing T4.1 (`backend/mcuaccel_backend/emit.py`), each the simplest reading
of DESIGN 6.5. (1) Address fields in generated code go through `MCUACCEL_ADDR(p)` (default
`(uint32_t)(uintptr_t)p` in `mcuaccel.h`) so the host bit-exactness test can map pointers to
offsets of a shared arena without changing the emitted text. (2) NEXT is never taken from
`build_commands`; the emitter links descriptors as `&ad_c[k+1]` in `ad_bind()`, last one 0.
(3) A depthwise layer whose x is the network `input` falls back to software (input alignment is
not guaranteed); neither workload hits this. (4) The accelerated build contains only the mcugrad
kernels that its fallback ops use; fairness (SPEC §7) is checked textually on the kernels that
remain, the objdump comparison is M5's. (5) Host test images are the head of the committed
baseline `testset.c`. (6) Per-chain functions take only the `input`/`output` they patch.
Options rejected: emitting NEXT from the backend (needs final addresses at emit time) and a
separate accelerated driver header (duplicates the register map).
(7) T4.3: the emitted model.c carries `MCUACCEL_P`, checked against CONFIG at bind (a P sweep
would otherwise run P=8 firmware on a P=4 build silently). (8) T4.3: the M5 fairness check is a
measured per-op cycle comparison of the profile builds, not an objdump hash (kernels are inlined).

## D20 Orchestrator: M2 red-team fixes, one of which changes timing toward DESIGN 7.2 (2026-10-19)
T2.3 found a blocker (a credit returned in the ABORT cycle left ctrl at 3 credits; the next chain
overfilled the 2-deep requant FIFOs and FLUSH never ended) and that the credit loop had two
register stages, so a credit-stalled request issued 2 cycles after the requantizer pop instead of
the 1 cycle DESIGN 7.2 specifies. Options: keep the registered credit_ret and log the slower
timing as the design; make credit_ret combinational so the RTL matches the normative DESIGN text.
Choice: combinational (`credit_ret = pop`), plus credit reset to 2 on start and credit_ret ignored
in ABORT, m_req gated by rst, ERR_ADDR cleared on START/CLEAR. Reason: the target is DESIGN 7.2,
written before m1; the model was not consulted. SPEC C4 logging: no full STAMP measurement
(m6_pred_vs_meas.csv) existed before this change, so the scored RTL is the fixed one. For
transparency, the uncommitted P = 8 smoke runs on the pre-fix RTL (a2e8c4b + working tree,
2026-10-19) gave C4(b) errors of -1.01 % (mnist: meas 18,928 vs pred 18,736) and +0.44 % (cifar:
meas 135,281 vs pred 135,876). Red-team timing shape (P = 4, conv 1x1, C = 4, O = 32, 4x4): ISSUE
588 -> 494 cycles, command total 770 -> 676.


## D19 Orchestrator: fallback-kernel fairness via pinned inlining; baseline re-measured (2026-10-19)
Problem: the M5 profile builds (`run_soc.py --profile`) showed the same fallback op costing a constant
few cycles more or less in the accelerated build than in the baseline (mnist transposes +28/+4,
maxpool op 2 +4, cifar avgpool +7), although the C text is identical (D18 test). Cause: GCC inlines
and register-allocates `mg_*` into `op_*` and `op_*` into `model_run` differently when the file holds
a different set of ops (e.g. a kernel called twice vs once), so identical text is not identical code.
SPEC §7 / DESIGN 6.5 require identical machine code for fallback ops.
Options: (a) all kernels external + `noinline` in one shared translation unit `mg_kernels.c` compiled
for both builds (the SPEC §7 text). Built and measured: fairness passed after (c) below, but it
removes constant propagation into the kernels, slowing everything: baseline mnist 13,579,626
(+11%), accelerated mnist 771,918 (+71%), because fallback ops dominate the accelerated runtime.
(b) leave as is, report the deltas: rejected, requirement is exact equality. (c) CHOSEN, in
`sw/build_fw.py::pin_inlining`, applied to every build including the baseline: `mg_*` kernels
`static inline __attribute__((always_inline))` (each kernel is inlined into its one `op_*`, with that
op's constants) and `op_*` `__attribute__((noinline))`. Each fallback `op_*` is then compiled from
its own text alone. Kernel and op source text is unchanged; only the attributes are added by a
regex at build time (the files under third_party/mcugrad are untouched).
Second source of difference, found with objdump: in the accelerated `model_run` the `ad_bind()`
call before the first op clobbered a0, so a `mv a0,input` landed inside the first op's profiled
interval (+4 cycles, op 0 mnist). Fix in `emit.py`: `input = ad_bind_once(input);` with
`ad_bind_once` noinline and returning its argument, so `input` is in a0 as in the baseline.
This amends D6 ("baseline = mcugrad model.c unchanged"): the baseline is now the same source with
the two attribute changes above, re-measured at 10 images x 3 runs (D17), same flags and SoC.
Before / after (cycles_median): baseline mnist 12,188,028 -> 11,383,383; baseline cifar
72,157,425 -> 69,169,620; accelerated P=8 mnist 451,480 -> 243,059; cifar 197,617 -> 197,613.
The mnist accelerated number nearly halves with no accelerator change: its runtime was dominated by
the maxpool/transpose fallbacks, which the old inlining compiled badly; the speedup over baseline
is 27.0x -> 46.8x on mnist (cifar 365x -> 350x). Anything quoting the old numbers is stale.
Result: `m5_profile_*_P8.json` fairness pass, per-inference cycles of every fallback op equal in
both builds (mnist ops 0,2,4,5; cifar op 13 avgpool; 3 inferences compared each).
Not done: the objdump identity check of DESIGN 6.5 (cycle equality is the criterion, D18 (8)); the
shared-TU wording in SPEC §7 / DESIGN 6.5 is not edited and should say "pinned inlining" instead.

## D21 Orchestrator: post-hoc notes from the final claims red-team T6.3 (2026-10-19)
T6.3 recomputed every claim from results/ independently. Findings that change no scored number:
(A) C4a rows that exceeded their pre-registered bias (DESIGN 7.4): mnist op_1 at P = 8 and 16
(measured over-prediction about 12.4 % against a registered 3.4 %) and cifar op_0 at P = 16 (6.2 %
against 1.9 %). Root cause: when the port term lam*(R+W) ties the requant term NV, the write of the
previous tile is granted during PSETUP, so PSETUP is hidden at ties as well as at strictly requant
bound pixels, which 7.4 counted only. (B) `CYCLES == sum(STAMP + lam + 1) + 1` on all 275 chain calls,
not `+ 2` as the frozen check (and commit d189571's message, which said 550 calls) stated; the START
acceptance cycle is not BUSY, RTL and STAMP are correct, DESIGN 7.2's `+ 2` is the CPU-visible term
and C4b is unaffected (at most 3 cycles per inference). (C) The first independent STAMP check
(D15 asked for at least three): a bus-level count equals STAMP on mnist op_1 P=8 (6356), mnist op_3
P=8 (7715), cifar op_0 P=16 (16616).
(A, continued) The script that scores this rule (results/deviations.json) lists six rows over the registered
bias + 0.5 pp, not three: the three above plus mnist op_3 P=16, cifar op_0 P=4 and P=8; they were not probed.
Options: (a) fix the model and rescore; (b) leave the model outputs as scored and add the
explanation; (c) loosen the check to `+ 1` silently. Choice: (b), plus keep the frozen `+ 2` check
in experiments/compare_m1.py and add the `+ 1` check beside it. Reason: SPEC C4 scores the frozen
m1 predictions; the model may be fixed post-hoc but is not, so the original numbers stand and the
paper/DESIGN 7.5 carry the post-hoc explanation. C4a stays confirmed (all rows within 20 %).
Also from T6.3, applied to the tooling: the m1 predicted speedups were computed against the
pre-D19 baselines, so the paper adds a post-hoc row (frozen C4b prediction + measured new
fallback-op cycles + bench window) next to the measurement; C2c is confirmed only with the baseline
firmware comparison for both networks; synthesis rows are deduplicated per seed and limited to the
newest RTL version of each file (documented in experiments/status.py); baseline JSON `kind` names D19
and records `tree_dirty`; values quoted from D19/D20 live in results/deviations.json, written by
experiments/make_deviations.py (which asserts they appear in this file).

Addendum (2026-10-24): the six rows have since been probed (experiments/probe_tie.py ->
results/m6_tie_probe.json; one RTL command per layer shape, bus STAMP equal to the measured STAMP).
prediction - measurement splits cycle for cycle into writes granted in the PSETUP cycle, PSETUP hidden
at requant-bound pixels (the registered term) and flush. The lam*(R+W) == NV tie fully explains four
rows: mnist op_1 P=8 and P=16, mnist op_3 P=16, cifar op_0 P=16. It does not explain cifar op_0 P=4
and P=8: they are strictly port bound with 0 tie writes, yet the previous pixel's pending write is
granted in PSETUP the same way (707 and 690 writes), hiding the setup cycle. The mechanism is thus
general: any pending write of the previous pixel granted in PSETUP. With all PSETUP-cycle writes
removed every row is within registered bias + 0.5 pp, and the rows registered at the default 0.5 %
keep at most 0.40 % error. Choice (b) stands; no scored number changes. DESIGN 7.5 carries the
per-row split.
