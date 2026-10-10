# corv32 — conventions for agents and humans

Read PLAN.md first. It is the source of truth for status and next steps.

## Environment
- `scripts/bootstrap.sh` fetches all tools (OSS CAD Suite, xPack GCC, Spike, RISCOF) into
  `tools/`, `third_party/`, `.venv/`. Nothing outside the repo. Then `source scripts/env.sh`.
- The repo path must not contain spaces (OSS CAD Suite wrappers and Verilator break).
- nextpnr's wrapper writes to `$HOME/.config`; the Makefile runs it with `HOME=.cache/home`.

## Hard rules
- No code from existing cores (PicoRV32, VexRiscv, etc.) in `rtl/`. Baselines live only in
  `third_party/` and are referenced by `syn/` and `experiments/` for comparison.
- Never fabricate numbers. Every number in README/paper comes from `results/` via a generator
  script, or is `\todo{MEASURE}` with an entry in HARDWARE_TODO.md.
- Decisions go in docs/DECISIONS.md (options, choice, reason). Repeated failures go in
  docs/BLOCKERS.md.
- No Claude/AI attribution trailers in commits or PRs. AI assistance is disclosed once, in
  the paper acknowledgment.

## RTL style
- SystemVerilog subset accepted by Yosys `read_verilog -sv`, Verilator `--lint-only -Wall`,
  and Verilator simulation: `logic`, `always_ff`, `always_comb`, `localparam`, packed vectors.
  No interfaces, no classes, no structs in ports, no `unique case`.
- One module per file, file name = module name. Synchronous active-high reset `rst`.
- Lint must be clean: `make lint`.

## Layout
- `rtl/core/` core, `rtl/soc/` SoC peripherals, `tb/` cocotb + Verilator harnesses,
  `formal/` riscv-formal glue, `verif/` RISCOF plugin and lockstep, `sw/` firmware,
  `syn/` synthesis flows, `experiments/` scripts, `results/` raw data (CSV/JSON with seed,
  commit, env), `paper/` LaTeX.

## Git
- Conventional Commits (feat, fix, test, docs, perf, refactor, bench, paper), message says
  why and references the milestone (e.g. `feat(core): ... [M2]`). Small commits, never squash.
- Tag milestones `m1`..`m7`, release `v0.1.0`.
