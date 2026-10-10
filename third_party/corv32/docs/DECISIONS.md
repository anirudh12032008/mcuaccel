# Decisions

Each entry: options, choice, reason.

## D1 Repository location
- Options: (a) the app's scratch workspace; (b) `~/corv32`.
- Choice: (b).
- Reason: the scratch path contains a space ("Application Support"), which breaks the OSS CAD
  Suite wrapper scripts and Verilator's Perl driver (observed: `sby`, `verilator` failed with
  "No such file or directory: /Users/anirudh/Library/Application"). The scratch workspace is
  also deleted with the session, which conflicts with "PLAN.md lets a fresh session resume".

## D2 Toolchain provisioning
- Options: Homebrew system packages; project-local prebuilt bundles; build everything from source.
- Choice: project-local. YosysHQ OSS CAD Suite 2026-10-03 (yosys, sby, solvers, verilator,
  nextpnr, cocotb), xPack riscv-none-elf-gcc 15.2.0-1 (SHA-256 verified), Spike and dtc built
  from pinned source, Python venv via uv. All under `tools/`, `third_party/`, `.venv/`.
- Reason: rule 4 (no sudo, prefer local). Pinned versions make results reproducible.

## D3 Third-party sources are fetched, not vendored
- Options: commit third_party/; git submodules; pinned tarballs.
- Choice: pinned tarballs listed in `scripts/pins.txt`, fetched by `scripts/bootstrap.sh`.
- Reason: keeps baseline core code out of our history (from-scratch rule), avoids ~60 MB of
  vendored code, and pins exact commits.

## D4 Architectural test suite version
- Options: riscv-arch-test default branch `act4` (new ACT4 framework); tag 3.9.1.
- Choice: 3.9.1.
- Reason: RISCOF consumes the 3.x `riscv-test-suite/` layout; ACT4 has a different flow.

## D5 RISCOF dependencies
- Options: PyPI riscof/riscv-isac/riscv-config; pinned GitHub sources.
- Choice: pinned GitHub sources.
- Reason: PyPI riscof 1.25.x fails at import against PyPI riscv-config/riscv-isac
  (`ImportError: cannot import name 'warl_interpreter'`, then `'preprocessing'`).

## D6 Claim structure
- Options: (a) keep the brief's combined claim as the research claim; (b) brief's claim as an
  engineering acceptance gate (E1) plus a narrower research claim.
- Choice: (b). C1 = verification-yield study over a pre-registered planted-bug corpus;
  C2 = same-FPGA same-flow baseline comparison; C3 = cost of reaching 2 CoreMark/MHz.
- Reason: docs/PRIOR_ART.md shows every element of the brief's claim is already
  demonstrated (Ibex, VexRiscv > 2 CoreMark/MHz; riscv-formal, arch tests and Spike
  lockstep are routine). Claiming the combination as novel would be wrong.

## D7 Keep RISCOF despite upstream deprecation
- Options: RISCOF + riscv-arch-test 3.9.1; ACT4; both.
- Choice: RISCOF + 3.9.1 (Spike reference). Deprecation listed as a limitation (SPEC §7.1).
- Reason: the brief names RISCOF; ACT4 is a different flow and doubles integration work.

## D8 FPGA target and flow
- Choice: LFE5U-25F-6 CABGA381 (ULX3S), Yosys synth_ecp5 + nextpnr-ecp5 out-of-context,
  register-wrapped cores, 10 seeds. Details DESIGN §10.
- Reason: ECP5 fully open flow; 25F large enough for all baselines; ULX3S is a common board.

## D9 Baseline variants
- Choice: PicoRV32 im-small and im-fast; VexRiscv default (RV32IM, caches), Lite, Min
  (pregenerated, pythondata-cpu-vexriscv 642ecfe). Details DESIGN §10.
- Reason: no cache-less RV32IM VexRiscv is pregenerated and regenerating needs Scala/SBT
  (not installed); variants bracket the like-for-like point, reported as a limitation.

## D10 Flash boot
- Choice: real SPI (cmd 0x03) behind a memory-mapped read controller; boot ROM copies the
  image to RAM. DESIGN §8.3.
- Reason: matches ULX3S hardware and exercises the wait-state side of the bus.

## D11 No branch predictor (initially)
- Choice: static not-taken, EX resolution, 1-cycle taken penalty.
- Reason: DESIGN §7.8 estimate reaches 2.0 CoreMark/MHz without one; gate in Step 3
  (measured instruction count) decides whether to add static BTFN.

## D12 SPEC vs Spike divergences: document, do not mimic
- Options: (a) make corv32 copy Spike (pmp/tselect CSRs readable, mtimecmp resets 0,
  16-bit mtval for compressed-pattern illegal words); (b) keep SPEC behaviour and keep the
  divergent cases out of lockstep.
- Choice: (b). SPEC §4.2/§4.3/§4.5 describe corv32; `tb/model/rv32.py` already matches it.
  Lockstep avoids each case by construction: the generator never accesses pmp*/tselect, only
  uses 16-bit-pattern illegal words with a zero upper half (mtval agrees), and writes
  mtimecmp = all ones first. Probed on the pinned Spike: mtimecmp reads 0 at reset;
  pmpcfg0 reads 0x1f; tselect reads 0; mtval for 0xDEAD0001 is 0x0001.
- Reason: PMP/trigger CSRs are out of scope (no C, no PMP; SPEC §4.1) and an all-ones
  mtimecmp reset is the safer hardware default; mimicking Spike would add logic for no
  verification value.
