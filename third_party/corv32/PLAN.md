# PLAN — corv32 (source of truth)

A fresh session resumes from here. Status legend: [x] done (evidence linked), [ ] open.

## Environment
- Repo: `/Users/anirudh/corv32` (moved from a scratch dir; see DECISIONS D1).
- `scripts/bootstrap.sh` then `source scripts/env.sh`. Versions in docs/ENVIRONMENT.md.

## Steps
- [x] Step 0  Setup: toolchain, CLAUDE.md, PLAN.md, LICENSE, CI, Makefile (8b25c4f)
- [x] Step 1  Research: docs/PRIOR_ART.md, paper/refs.bib, falsifiable claim in docs/SPEC.md (6abc447, c3a89ed)
- [x] Step 2  Design: docs/DESIGN.md (c3a89ed)
- [x] Step 3  Walking skeleton (RTL -> sim -> firmware -> formal -> synth, in CI)
- [ ] Step 4  Build milestones
  - [ ] M1 single-cycle RV32I + cocotb tests (tag m1)
  - [ ] M2 five-stage pipeline, forwarding, hazards, branches (tag m2)
  - [ ] M3 M extension, CSRs, traps, machine-mode interrupts (tag m3)
  - [ ] M4 verification: riscv-formal/SBY, RISCOF, Spike lockstep 1M instr, coverage (tag m4)
  - [ ] M5 SoC: UART, timer, GPIO, flash boot, Verilator model (tag m5)
  - [ ] M6 Yosys/nextpnr Fmax + LUTs vs PicoRV32, VexRiscv on same FPGA (tag m6)
  - [ ] M7 mcugrad-generated C on the core, measured inference cycles (tag m7)
- [ ] Step 5  Verify: everything green
- [ ] Step 6  Experiments: baselines, CoreMark/MHz, ablations, variance
- [ ] Step 7  Paper: 6-page IEEEtran, macros from results/, reviewer pass
- [ ] Step 8  Ship: README, HARDWARE_TODO, tag v0.1.0, FINAL_REPORT.md

## Next action (2026-10-04, resumed)
All five worktree branches merged to main and verified there: `make lint test` (237 passed),
`make formal-sc` (10 checks PASS), `make synth-sc macros` (524 LUT4 / 160 FF / 85.08 MHz, seed 1,
reproduced). Step 3 walking skeleton done; CoreMark gate: BTFN does NOT trigger (predicted, not measured).
Open follow-ups from agents:
- [x] SPEC 4.2/4.3/4.5 wording (Spike mtimecmp resets 0; 16-bit mtval; pmp/tselect readable on
  Spike) fixed, decision D12: keep SPEC behaviour, lockstep avoids each case by construction.
- [x] DESIGN 7.8 now uses measured N / mix from results/coremark_mix (predictions labelled);
  9.4 notes mtimecmp all-ones first and rvfi_intr after any trap.
- [x] `make lockstep` / `make lockstep-selftest`; `needs_tools` mark registered in root pytest.ini.
  `make lockstep` needs LOCKSTEP_DUT (Verilator SoC, M5) to compare a DUT; otherwise Spike-only.
- M1: integrate corv32_sc on shared modules (alu/decode/regfile/lsu), tb/sc on sw/tests, red-team review, tag m1.
- Then M2 per DESIGN section 7.
Stale worktrees under .claude/worktrees/ can be removed (`git worktree remove`).

## Log
- 2026-10-04 Step 0-2 done. Claim restructured (D6): E1 gate = brief; C1 = verification-yield.
- M7 input: mcugrad commit b0b0ee9, firmware/generated/mnist_cnn/{model.c,model.h,testset.c}
  (int8, no float/libm/malloc; model_run(const int8_t*, int8_t*); arena 7,840 B).
