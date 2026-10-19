#!/usr/bin/env python3
"""Measured overlay figure: results/m6_c5.json + results/m6_pred_vs_meas.csv -> results/m6_pred_vs_meas.png.
(a) per-inference sum of STAMP vs P, frozen m1 prediction (line) and measurement (markers);
(b) every scored command: predicted vs measured cycles with the +-20 % C4a band. Derived figure, no new numbers."""
import csv, json, os
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

R = Path(__file__).resolve().parent.parent / "results"
c5 = json.loads((R / "m6_c5.json").read_text())
rows = list(csv.DictReader((R / "m6_pred_vs_meas.csv").open()))
fig, (a, b) = plt.subplots(1, 2, figsize=(7.0, 2.6))
for (m, d), col in zip(c5["models"].items(), ("tab:blue", "tab:orange")):
    Ps = sorted(map(int, d["measured_sum_stamp"]))
    a.plot(Ps, [d["predicted_sum_stamp"][str(P)] for P in Ps], "-", color=col, label=f"{m} pred.")
    a.plot(Ps, [d["measured_sum_stamp"][str(P)] for P in Ps], "o", color=col, mfc="none", label=f"{m} meas.")
a.set(xscale="log", yscale="log", xlabel="P", ylabel="sum of STAMP per inference (cycles)", xticks=[1, 2, 4, 8, 16])
a.set_xticklabels([1, 2, 4, 8, 16])
a.legend(fontsize=6)
for P, mk in zip((1, 2, 4, 8, 16), "ovsD^"):
    s = [r for r in rows if int(r["P"]) == P]
    b.plot([float(r["meas_cycles"]) for r in s], [float(r["pred_cycles"]) for r in s], mk, ms=3, label=f"P={P}")
lo, hi = 1e2, 1e5
b.plot([lo, hi], [lo, hi], "k-", lw=0.6)
b.fill_between([lo, hi], [0.8 * lo, 0.8 * hi], [1.2 * lo, 1.2 * hi], color="gray", alpha=0.2, lw=0)
b.set(xscale="log", yscale="log", xlabel="measured STAMP (cycles)", ylabel="predicted (cycles)", xlim=(lo, hi), ylim=(lo, hi))
b.legend(fontsize=6)
for ax in (a, b):
    ax.tick_params(labelsize=7)
    ax.xaxis.label.set_size(7); ax.yaxis.label.set_size(7)
fig.tight_layout()
fig.savefig(R / "m6_pred_vs_meas.png", dpi=200)
print("results/m6_pred_vs_meas.png")
