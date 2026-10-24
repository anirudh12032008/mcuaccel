# PLAN — mcuaccel (source of truth)

A fresh session resumes from here. Status legend: [x] done (evidence linked), [ ] open.
Read CLAUDE.md (conventions), docs/SPEC.md (claims C1-C5), docs/DESIGN.md (interfaces, timing,
module list, test plan), docs/DECISIONS.md (D1-D21) before touching anything.

## Environment
- Repo: `/Users/anirudh/mcuaccl`. `scripts/bootstrap.sh` then `source scripts/env.sh`.
  Versions: docs/ENVIRONMENT.md. No `origin` remote (local repository only; nothing to push).
- Dependencies: mcugrad e9f6283 (submodule), corv32 e1d0bc0 (vendored subset), PicoRV32 ef203c2
  (vendored). Datasets copied from ../mcugrad/data by bootstrap.
- Git writes need the sandbox off (docs/BLOCKERS.md); only the orchestrator commits.
- Measured so far (probe, not a result file yet): PicoRV32 in the SPEC §7 config on LFE5U-85F:
  1853 LUT4 / 2735 TRELLIS_COMB, 858 FF, 4 MULT18X18D, Fmax 82-87 MHz (2 nextpnr runs).

## Steps
- [x] Step 0  Setup (b9056dc, b3df26a, ebed0de): toolchain, CLAUDE.md, LICENSE, CI, Makefile, smoke tests
- [x] Step 1  Research (2412938): docs/PRIOR_ART.md (39 sources), paper/refs.bib; claim in docs/SPEC.md
- [x] Step 2  Design: SPEC v1.1, DESIGN v1.1 (red-team D15 applied: 1 blocker, 8 major, 12 minor), this plan
- [ ] Step 3  Walking skeleton (SoC + Verilator harness + baseline firmware + accel stub + backend
      executor + M1 model skeleton + synth of the stub), all in `make test`
- [ ] Step 4  Build milestones
  - [x] M1 model/m1.py, results/m1_predictions.csv + m1_summary.json; tag m1 BEFORE datapath RTL (C4).
        Red-team T1.2: 0 equation errors, 3 hand recomputations match; stale outputs fixed (1a63b3f).
        csv sha256 fd94f39a…; generated at 1f4fa47 (clean). Predicted speedup P=8: mnist 27.0, cifar 363.1.
  - [x] M2 rtl/accel/* per DESIGN §8, unit tests vs int8ops (tag m2). Datapath 53be20a, control a2e8c4b,
        red-team T2.3: 1 blocker (credit leak after abort) + credit-loop latency fixed in 31cb76a (D20).
  - [x] M3 tb/accel: 1000 random layers x 5 P, whole networks, chains, errors: 0 mismatches (tag m3)
  - [x] M4 backend: packing, splitting, descriptors, model.c emit, driver; executor == int8ops.
        emit.py + host bit-exactness + fairness tests (5af3876), red-team T4.3 0 blockers, fixes 44fd6a8;
        the emitted builds run on the real RTL in M5 (C2c, C1, profile). D19 pinned inlining in
        sw/build_fw.py. Open: T4.2 transpose folding (optional, default off, not used). Tagged m4.
  - [x] M5 SoC: both builds of both networks in Verilator, timing P=1..16, C4 per command and per network,
        C5, Amdahl shares (d189571). C2c confirmed: 100 images per network, 0 mismatching bytes vs run_int8
        and vs the baseline firmware, both networks (results/m5_bitexact.json). Tag m5.
  - [x] M6 syn/ + experiments/: ECP5 sweep committed (8bc8e6a); C3 refuted at P=8 (LUT4 over budget; see
        FINAL_REPORT), C4a confirmed (all rows within 20 %, some over their pre-registered bias: D21),
        C4b confirmed, C5 measured (results/m6_c5.json), T6.3 final claims red-team applied (D21,
        DESIGN 7.5). soc_ram maps to DP16KD block RAM (cycles unchanged, 436 tests pass); SoC synthesis
        at 10 seeds each: P=8 10,684 LUT4 / 35.8 MHz, no accel 3,392 LUT4 / 61.0 MHz. Tag m6.
  - [ ] Stretch: Tiny Tapeout-sized variant (P=1 or 2) through LibreLane/sky130, if time allows
- [x] Step 5  Verify: clean clone 2026-10-21 at 6263c64: lint clean, 435 passed, 1 skipped
      (test_whole_network: needs the git-ignored build/graphs cache from make test-slow; C2b/C2c in results/)
- [x] Step 6  Experiments: experiments/*.py -> results/*.csv|json, figures only from results/
- [x] Step 7  Paper: paper/main.tex (IEEEtran conference, 6 pages, T7.2 reviewer pass) and
      paper/journal/main.tex (IEEEtran journal, 14 pages); shared macros from results/
- [x] Step 8  Ship: README, HARDWARE_TODO.md, FINAL_REPORT.md, tag v0.1.0

## Task graph (who, model, dependencies, parallelism)

Roles: ORCH = orchestrator (Fable; integration, commits, judgment). S = Sonnet 5.5, O = Opus 5.5,
H = Haiku. "||" = runs in parallel with. Files owned are exclusive while a task runs.

### Step 3 walking skeleton (T3.x; all || except where noted)
- T3.1 SoC RTL (S): rtl/soc/{soc_top,soc_picorv32_adapter,soc_interconnect,soc_ram,soc_console}.sv,
  rtl/accel/mcuaccel_regs.sv, STUB rtl/accel/mcuaccel_top.sv (regs only: START -> DONE next cycle,
  no datapath, exempt from C4), tb/common/{bus_checker,ram_slave}.py, tb/soc cocotb smoke
  (firmware: putchar, tohost, rdcycle, accel ID read). Lint clean.
- T3.2 Sim + firmware (S): sim/sim_main.cpp + sim/Makefile (Verilator, DESIGN §4.4), sw/common
  (crt0.S, link.ld for 1 MiB RAM, platform.c implementing mcugrad's platform.h over the console,
  bench_main reuse), sw/tools (elf2hex from corv32), `make sim`, baseline builds of mnist_cnn and
  cifar_mobilenet model.c from the submodule, run in Verilator, measure cycles (BENCH_RUNS small
  at first), profile build (-DMCUACCEL_PROFILE around op_* calls) -> results/baseline_*.json.
  Depends on T3.1 for the first run; firmware/harness code can be written before.
- T3.3 Backend executor (S): backend/mcuaccel_backend/{graphs.py (build int8 graphs from mcugrad's
  pipeline, cache JSON under build/), pack.py (DESIGN §6.4), desc.py (§6.2), split.py, exec.py
  (pure-Python executor of descriptor semantics, reuses int8ops.requant)}, backend/tests: executor
  == int8ops on random layers (seeded), model.c reproduction test (DESIGN §10.2).
- T3.4 M1 model skeleton (S): model/m1.py implementing DESIGN §7/§9 for "os", "lb", "acc";
  tb/unit/test_m1_model.py (layer table == parsed model.c; hand-checked T_tile examples from §7.2).
  Predictions need T3.2's measured sw_cycles for the speedup rows; accelerator rows do not.
- T3.5 Plan red-team (O, read-only): attack PLAN/SPEC/DESIGN; ORCH fixes findings (commits).
- T3.6 Synth of the stub (H/S): syn/synth.sh + syn/wrap/mcuaccel_top_wrap.sv + syn/extract.py
  (from corv32), runs on the stub -> results/syn/ (flow check only). After T3.1.
Gate: `make test` green with SoC cocotb smoke, backend executor tests, m1 model tests; baseline
cycles for both networks in results/baseline_*.json.

### M1 (after T3.2, T3.4)
- T1.0 (S) sw/driver/mcuaccel.{h,c} (DESIGN §6.5: barriers, uintptr_t, poll) written and measured on
  the STUB accelerator before m1 so M1's driver constants come from the compiled code (D15).
- T1.1 (S) finalize m1.py with measured sw_cycles and driver constants; write
  results/m1_predictions.csv, results/m1_summary.json, figures; T1.2 (O) red-team M1 (model vs
  DESIGN §7 equations, hand recomputation of 3 layers). ORCH tags m1. No datapath RTL before this.

### M2 RTL (after m1; two implementers in worktrees, tests first)
- T2.1 (S, worktree A) datapath: mcuaccel_align, mcuaccel_mac_array, mcuaccel_requant,
  mcuaccel_writer, mcuaccel_wbuf, mcuaccel_pbuf + cocotb unit tests (DESIGN §10.1).
- T2.2 (S, worktree B) control: mcuaccel_fetch, mcuaccel_ctrl, real mcuaccel_top glue + unit tests
  (ctrl request stream vs Python golden generator from backend exec.py).
- T2.3 (O) red-team M2 diff; fixes as separate commits. Escalate any bug surviving two S attempts to O.
### M3 verification (after M2)
- T3.a (S, Verifier, did not write RTL) tb/accel random-layer campaign (DESIGN §10.2), chains,
  errors, stalls; results/m3_bitexact.csv. T3.b (S) whole-network layer-by-layer test (C2b).
  T3.c (O) red-team. Bugs -> back to M2 owners.
### M4 backend (|| with M2/M3 after T3.3)
- T4.1 (S) emit.py: accelerated model.c (DESIGN §6.5) + sw/driver/mcuaccel.{h,c}; fallback
  selection; -Werror host build of emitted C with a host stub of the accelerator (executor via ctypes
  or a Python-driven mock) for bit-exactness on the host. T4.2 (S) optional passes (transpose
  folding) behind a flag, default off. T4.3 (O) red-team.
### M5 SoC integration (after M2-M4)
- T5.1 (S) accelerated firmware builds of both networks; C2c (100 images); C1/C1b/C5 measurement
  scripts (experiments/run_soc.py) -> results/m5_*.json; profile build. T5.2 (O) red-team.
### M6 synthesis + experiments (after M5)
- T6.1 (S) syn/ for mcuaccel_top P in {1,2,4,8,16}, PicoRV32, soc_top with/without accel; seeds
  1..10 -> results/syn/*.csv. T6.2 (S, Experimenter) experiments/: speedup table, per-layer
  pred-vs-meas (`git show m1:results/m1_predictions.csv`), P ablation, Amdahl shares, variance;
  all outputs with seed, commit, environment. T6.3 (O) red-team of every claim vs evidence.
  Post-hoc (2026-10-24): experiments/probe_tie.py -> results/m6_tie_probe.json (C4a over-bias rows);
  syn/breakdown.sh (`make synth-breakdown`) -> results/syn/accel_breakdown.json (per-module LUTs).
### Step 7 paper (after M6)
- T7.1 (S) paper/main.tex IEEEtran + experiments/make_macros.py -> paper/numbers.tex; figures from
  results/. T7.2 (O then Fable) paper reviewer: every number traced, every citation opened.
### Step 8 ship
- T8.0 `make reproduce` = sims + experiments + synth + macros + paper (D15); clean-clone check script.
- T8.1 (S) README quickstart, architecture diagram, results table, measured-vs-pending list;
  HARDWARE_TODO.md (FPGA wall-clock + power procedure); FINAL_REPORT.md. ORCH tags v0.1.0.

## Next action
2026-10-21: all claims scored (C1, C1b, C2a-c, C4a, C4b confirmed; C3 refuted; C5 descriptive); both
papers built; clean-clone check passed; tags m4-m6 and v0.1.0.
Before submission (author): push m1 to a public remote for an external timestamp; board wall-clock run
(HARDWARE_TODO.md items 1-2).

## Log
- 2026-10-24 Post-hoc: six-row PSETUP probe (results/m6_tie_probe.json; tie explains 4 of 6 rows, cifar
  op_0 P=4/8 port bound, same PSETUP write grant; D21 addendum, DESIGN 7.5). Per-module LUT breakdown
  (results/syn/accel_breakdown.json; DESIGN 8.12 note). Fig. 1 diagram; journal review pass. No scored numbers changed.
- 2026-10-21 C2c confirmed (cifar baseline-firmware comparison, 0 mismatches). SoC synth at 10 seeds,
  soc_ram BRAM fix committed. Conference paper reviewed (T7.2); journal version written. Tags m4-m6.
- 2026-10-19 T6.3 final claims red-team applied: D21 (PSETUP tie, CYCLES identity +1, independent STAMP
  check), paper post-hoc rows and deviations paragraph, C2c needs the baseline-firmware comparison,
  synthesis rows deduplicated per seed / newest RTL version. M5/M6 results in d189571. No numbers changed.
- 2026-10-19 m2 tagged. Pre-fix synth P=8: 7297 LUT4 > C3's 6000 (provisional, rerun on 31cb76a).
- 2026-10-17 m1 tagged. cifar baseline 10x3 = 72,157,425 cycles/inf (runs identical). M4 emitter done.
- 2026-10-10 Baselines measured: mnist 12.19M cycles/inf (10x3), cifar 72.16M (2x1, rerun pending);
  fallback ops mnist 433k cycles, cifar avgpool 63k. M1 provisional: accel 18.5k / 135.7k cycles at P=8.
- 2026-10-10 Plan red-team done; D15 applied to SPEC/DESIGN (C4/C5 reworked, test sets, driver before m1).
- 2026-10-10 Steps 0-2 done. Claim framing D14. Probe: PicoRV32 1853 LUT4 / 858 FF / 4 DSP / ~85 MHz.
