# mcuaccel — conventions for agents and humans

Read PLAN.md first. It is the source of truth for status and next steps.

## What this is
An int8 CNN accelerator (conv, depthwise, fully connected) memory-mapped into a RISC-V SoC,
bit-exact with mcugrad's int8 simulator, with a mcugrad backend that emits accelerator commands
and driver C. Research project: every number in results/ and paper/ is real and reproducible.

## Environment
- `scripts/bootstrap.sh` fetches tools (OSS CAD Suite, xPack RISC-V GCC) into `tools/` and creates
  `.venv/`. If a sibling `../corv32/tools` exists it is reused (same pinned versions). Then
  `source scripts/env.sh`. Versions: docs/ENVIRONMENT.md.
- Repo path must not contain spaces. nextpnr writes to `$HOME/.config`; run it with `HOME=.cache/home`.
- Dependencies: `third_party/mcugrad` (git submodule, pinned), `third_party/corv32` (vendored subset,
  commit in third_party/corv32/VENDORED.md), `third_party/picorv32` (vendored, pinned).
- Git writes (`add`, `commit`, `tag`) are done by the orchestrator only; the sandbox blocks `.git`
  writes for agents. Agents leave their work in the tree and report.

## Hard rules
- The accelerator RTL in `rtl/accel/` is from scratch. Nothing from other accelerators
  (NVDLA, Gemmini, VTA, CFU, ...) goes in there; they are prior art in docs/PRIOR_ART.md only.
- Bit-exactness is against `mcugrad.quant.int8ops` / `mcugrad.quant.sim.run_int8` (the spec).
  Rounding is TFLite/gemmlowp requant: SRDHM then RDBPOT, documented in mcugrad's mg_int8.h.
- Never fabricate numbers. Every number in README/paper comes from results/ through a generator
  script, or is `\todo{MEASURE}` with an entry in HARDWARE_TODO.md.
- Decisions go in docs/DECISIONS.md (options, choice, reason). Repeated failures in docs/BLOCKERS.md.
- No Claude/AI attribution trailers in commits or PRs. AI assistance is disclosed once, in the
  paper acknowledgment.

## RTL style
- SystemVerilog subset accepted by Yosys `read_verilog -sv`, Verilator `--lint-only -Wall`, and
  Verilator simulation: `logic`, `always_ff`, `always_comb`, `localparam`, packed vectors. No
  interfaces, no classes, no structs in ports, no `unique case`.
- One module per file, file name = module name, prefix `mcuaccel_` (accelerator) / `soc_` (SoC).
  Synchronous active-high reset `rst`. Lint must be clean: `make lint`.
- Bus protocol: corv32 DESIGN section 3 (req/gnt/rvalid, one outstanding), restated in docs/DESIGN.md.

## Layout
- `model/` M1 Python architecture exploration and bandwidth model (writes results/m1_*).
- `rtl/accel/` accelerator, `rtl/soc/` SoC (RAM, interconnect, CPU adapter, tohost/UART-lite).
- `tb/` cocotb tests (Verilator backend): `tb/unit`, `tb/accel`, `tb/soc`.
- `sim/` Verilator C++ harness for full-system runs (fast whole-network inference).
- `backend/` Python package `mcuaccel_backend`: int8 IR graph -> commands + driver C, software fallback.
- `sw/` firmware: `common/` (crt0, link.ld, bench loop), `driver/`, generated apps under build/.
- `syn/` Yosys/nextpnr ECP5 flows and LUT/DSP extraction. `experiments/` scripts, `results/` raw
  CSV/JSON with seed, commit, environment. `paper/` LaTeX (IEEEtran), numbers via generated macros.

## Python
- 3.12, type hints. numpy allowed in `model/`, `experiments/`, `tb/`, `backend/tests`. The backend
  imports mcugrad as a library (`third_party/mcugrad` on `sys.path` via `scripts/env.sh`).
- Tests are pytest under `tb/` and `backend/tests/`. `make test` runs them all.

## Git
- Conventional Commits (feat, fix, test, docs, perf, refactor, bench, paper); the message says why and
  references the milestone, e.g. `feat(accel): ... [M2]`. Small commits, never squash, never rewrite.
- Tag milestones `m1`..`m6`, release `v0.1.0`. Update PLAN.md and CHANGELOG.md in the commit that
  completes a milestone.
