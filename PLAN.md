# PLAN — mcuaccel (source of truth)

A fresh session resumes from here. Status legend: [x] done (evidence linked), [ ] open.

## Environment
- Repo: `/Users/anirudh/mcuaccl`. `scripts/bootstrap.sh` then `source scripts/env.sh`.
  Versions in docs/ENVIRONMENT.md. No `origin` remote exists (local repository only).
- Dependencies: mcugrad e9f6283 (submodule), corv32 e1d0bc0 (vendored subset), PicoRV32 (vendored).

## Steps
- [ ] Step 0  Setup: toolchain, CLAUDE.md, PLAN.md, LICENSE, CI, Makefile
- [ ] Step 1  Research: docs/PRIOR_ART.md, paper/refs.bib, falsifiable claim in docs/SPEC.md
- [ ] Step 2  Design: docs/DESIGN.md, full plan below, plan red-teamed
- [ ] Step 3  Walking skeleton (model -> RTL -> cocotb -> backend -> SoC sim -> synth, in CI)
- [ ] Step 4  Build milestones
  - [ ] M1 architecture exploration in Python, predicted speedup written down (tag m1)
  - [ ] M2 RTL: conv, depthwise, fc, requant identical to mcugrad (tag m2)
  - [ ] M3 cocotb verification, bit-exact on random layers and whole networks (tag m3)
  - [ ] M4 mcugrad backend: commands + driver C, software fallback (tag m4)
  - [ ] M5 SoC integration, full-system inference in Verilator (tag m5)
  - [ ] M6 synthesis, measured speedup vs prediction, parallelism ablations (tag m6)
  - [ ] Stretch: Tiny Tapeout-sized variant through LibreLane
- [ ] Step 5  Verify: everything green
- [ ] Step 6  Experiments: baseline, main result, ablations, variance
- [ ] Step 7  Paper: 6-page IEEEtran, macros from results/, reviewer pass
- [ ] Step 8  Ship: README, HARDWARE_TODO, tag v0.1.0, FINAL_REPORT.md

## Next action
Step 0 in progress (2026-10-10).

## Log
- 2026-10-10 Project started. D1-D6 recorded in docs/DECISIONS.md.
