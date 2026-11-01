# Hardware TODO (measurements that need a board or instrument)

Each entry: exact procedure, expected output format, file path where measured data must be saved.
Paper cells for these stay `\todo{MEASURE}` until the file exists.

Everything in README/paper is simulation (Verilator) or synthesis (Yosys + nextpnr, no board). The items
below are not done. Any number that depends on them is `\todo{MEASURE}` in the paper, "pending" in README.

## 1. Publish the pre-registration externally (done 2026-11-01, results/prereg_publication.json)
The repository has no remote, so the m1 timestamp is only as good as local git. Before submission:
1. `git rev-parse m1^{}` and `git show m1:results/m1_predictions.csv | shasum -a 256` (current values are
   printed in README.md and FINAL_REPORT.md; they must match).
2. Publish both (and ideally the tag) somewhere with an independent timestamp: a public git remote with the
   tag pushed, an OSF/Zenodo deposit, or an arXiv/ancillary-file note. Record the URL in the paper.
Output: `{"url": ..., "date": ..., "commit": ..., "sha256": ...}` in `results/prereg_publication.json`.

## 2. FPGA wall-clock speedup (ECP5 board, LFE5U-85F)
Simulation gives cycles; wall time = cycles / Fmax. SPEC section 6 C3 reports wall-time speedup = S x Fmax ratio,
and that needs the real clock.
Procedure:
1. Board: any LFE5U-85F-based board (e.g. ULX3S 85F). Toolchain: the OSS CAD Suite pinned in docs/ENVIRONMENT.md.
2. Build the SoC at the chosen clock: `bash syn/synth.sh soc 8 <seed>` gives the post-route Fmax per seed; pick the
   best passing seed, add pin constraints (LPF) for clock, UART, LEDs, program with `openFPGALoader`.
   The SoC build in results/syn is out-of-context with 256 KiB RAM; the board build needs RAM that fits the part.
3. Firmware: `sw/` baseline and accelerated builds of both networks, same images (D6, D19). Report cycles via
   `rdcycle` over UART exactly as in simulation; they must equal the Verilator numbers (cycle-exact check).
4. Wall clock: toggle a GPIO at start/end of the timed window and measure with a logic analyser (or count
   with an on-board free-running counter at the known clock). 10 images x 3 runs per build, report min/median/max.
5. Output: `results/hw_wallclock.json` with per-build per-run cycles (UART), seconds (analyser), clock Hz, board, seed, commit.
6. Report speedup_wall = t_baseline / t_accelerated, and compare with S x (Fmax_with / Fmax_without).

## 3. Power / energy
Not simulated, no claim made. Procedure: board with a current-sense header or inline shunt (or a USB power meter
on the 5 V input as an upper bound), measure idle (configured, CPU in a loop) and during inference for baseline and
accelerated builds at the same clock; energy per inference = (P_run - P_idle) x t. Report the measurement setup and
that the rail includes board overhead. Do not use vendor power estimators (ECP5 has no open one).
Output: `results/hw_power.json` with idle/run watts, seconds, joules per inference, instrument and setup description.

## 4. Other items SPEC does not cover in simulation
- Real SoC timing closure: results/syn is out-of-context at a 50 MHz target; I/O, PLL and real-RAM timing are untested.
- Real memory (SDRAM/PSRAM) latency: the cycle model (C4) is for the simulated RAM of SPEC section 7. A slower RAM changes S.
- Hardware behaviour of abort/error recovery under real bus faults (tested only with injected faults).
- ASIC/sky130 stretch variant (PLAN, optional) not attempted unless noted in FINAL_REPORT.md.
