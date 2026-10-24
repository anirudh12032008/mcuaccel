# Changelog

## v0.1.1 (2026-10-24)
- C4a post-hoc root cause for all six rows over their registered bias (experiments/probe_tie.py,
  results/m6_tie_probe.json): setup-cycle write grants; the tie is a special case. Predictions unchanged (D21).
- C3 post-hoc LUT attribution per module, P=1..16 (syn/breakdown.sh, `make synth-breakdown`,
  results/syn/accel_breakdown.json): the P-independent sequencer dominates.
- Paper: conference Fig. 1 as a TikZ block diagram; journal reviewer pass (15 pages); both cite the above.
- `make paper` / `make paper-journal` keep their logs; DESIGN 7.5/8.12 notes, D21 addendum, FINAL_REPORT section.

## v0.1.0 (2026-10-21)
- Project skeleton (Step 0).
- M1: analytic cycle model, pre-registered predictions frozen at tag `m1` (results/M1_PREDICTIONS.md).
- M2-M3: accelerator RTL, 0 mismatching bytes over 5000 random layers at P=1..16 (tags m2, m3).
- M4: accelerated model.c emitter, driver, host bit-exactness and fairness tests (tag m4).
- M5: SoC runs at P=1..16; C1, C1b, C2c confirmed, C5 measured (tag m5).
- M6: ECP5 synthesis (C3 refuted), C4a/C4b confirmed, soc_ram on block RAM (tag m6).
- Paper: conference (6 pages) and journal (14 pages) versions, every number from results/.
