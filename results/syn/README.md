# results/syn: synthesis results (Yosys + nextpnr-ecp5)

## How to run

```
source scripts/env.sh
make synth DESIGN=accel P=8 SEED=1        # or: bash syn/synth.sh accel 8 1
```

`DESIGN` is one of `accel` (`mcuaccel_top_wrap`, parameter `P` via `chparam`), `picorv32`
(`picorv32_wrap`, SPEC 7 configuration), `soc` (`soc_top`, `RAM_BYTES=262144`, accelerator at `P`),
`soc_noaccel` (`soc_top` built with `-DMCUACCEL_NONE`: accelerator ties off, RAM at 256 KiB).
Flow: Yosys `synth_ecp5`, then `nextpnr-ecp5 --85k --package CABGA381 --speed 6 --out-of-context
--freq 50 --timing-allow-fail --seed SEED --report`, with `HOME=.cache/home`. Run one flow at a time
(ABC is heavy; 1-2 minutes each on a loaded machine). Intermediate files: `build/syn/<design>_P<P>_s<SEED>/`.

Each run appends one JSON line to `results/syn/<design>_P<P>.jsonl` (`picorv32` and `soc_noaccel`
have no P: `<design>.jsonl`). `syn/extract.py` builds the row.

## Row schema

| field | meaning |
|---|---|
| design, P, seed | run identity (`P` is null for `picorv32`, `soc_noaccel`) |
| freq_target | nextpnr `--freq` (MHz) |
| fmax_mhz, timing_met | last "Max frequency for clock" in the nextpnr log; met if >= target |
| lut4 | `TRELLIS_COMB` used (includes carry-chain halves), SPEC 5 |
| ff | `TRELLIS_FF` used, raw |
| ff_port_bits | upper bound on wrapper FFs (port bits except clk; `accel`, `picorv32` only). SPEC 5 FF = ff minus wrapper FFs; FFs on ports tied to constants are optimised away, so subtract only the ones that exist |
| dp16kd, mult18x18d, lutram | `DP16KD`, `MULT18X18D`, `TRELLIS_RAMW` used |
| yosys_cells | Yosys `stat` counts: LUT4, CCU2C, PFUMX, L6MUX21, TRELLIS_FF, DP16KD, MULT18X18D |
| utilization | full nextpnr utilization map |
| device, package, speed | LFE5U-85F, CABGA381, 6 |
| commit, dirty, date | `git rev-parse HEAD` (`unknown` outside a repo), uncommitted tracked changes, UTC time |
| tools | Yosys and nextpnr versions |
| wall_s | wall seconds of Yosys + nextpnr |
| note | present only on rows built from the walking-skeleton accelerator stub |

## Stub rows are flow checks, not results

Rows with `"note": "walking-skeleton stub"` (designs `accel` and `soc`, while the accelerator is the stub) come from the walking-skeleton `mcuaccel_top`, which has no datapath. They only
show that the flow runs end to end. Do not quote them; real accelerator rows come after M2. The
`picorv32` and `soc_noaccel` rows do not involve the accelerator and are real baseline measurements
(single seed; Fmax claims need seeds 1..10).

## M6 sweep (T6.1)

Driver: `experiments/run_syn_sweep.sh [JOBS]` (accel P=8 and soc P=8 seeds 1..10, accel P in {1,2,4,16}
seeds 1..3, picorv32 and soc_noaccel seeds 1..10; at most JOBS (default 3) synth.sh flows at once).
`SKIP="accel 8 1"` drops one job line. Environment: `source scripts/env.sh`, `HOME=.cache/home`
(set by synth.sh); Yosys 0.69+187 (2f08661dd), nextpnr-ecp5 0.11.1-47-ge2fe86b3, OSS CAD Suite
from `../corv32/tools`. One run is about 9 min wall on the loaded dev machine (load average >300).

`results/syn/stub/` holds superseded data, kept on purpose: the walking-skeleton stub rows, the
single-seed picorv32/soc_noaccel probes, and `accel_P8_prefix_rtl.jsonl` (seeds 1..9 of the real
datapath RTL before the credit_ret / m_req / ERR_ADDR fix; not final, do not quote).
Pre-fix accel P=8 (9 seeds): LUT4(TRELLIS_COMB) 7297 in every seed, FF 3543, DP16KD 11,
MULT18X18D 36, Fmax median 60.65 (59.25..62.49) MHz.
