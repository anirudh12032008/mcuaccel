"""Cycles vs P per layer ("os" and the other archs) from results/m1_predictions.csv only.
Usage: python model/plot_m1.py [results_dir]  -> results_dir/m1_cycles_vs_P.png"""
import csv
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

d = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "results")
acc = defaultdict(float)   # (model, arch, op, P) -> sum of t_cmd over commands
for r in csv.DictReader(open(d / "m1_predictions.csv")):
    acc[r["model"], r["arch"], int(r["op"]), int(r["P"])] += int(r["t_cmd"])
models = sorted({k[0] for k in acc})
fig, axs = plt.subplots(len(models), 3, figsize=(13, 4 * len(models)), squeeze=False)
for i, m in enumerate(models):
    for j, arch in enumerate(("os", "lb", "acc")):
        ax = axs[i][j]
        for op in sorted({k[2] for k in acc if k[0] == m}):
            ps = sorted({k[3] for k in acc if k[:3] == (m, arch, op)})
            ax.plot(ps, [acc[m, arch, op, p] for p in ps], marker="o", label=f"op_{op}")
        ax.set(xscale="log", yscale="log", xlabel="P", ylabel="cycles (sum of STAMP)",
               title=f"{m} / {arch}", xticks=[1, 2, 4, 8, 16], xticklabels=[1, 2, 4, 8, 16])
        ax.legend(fontsize=6, ncol=2)
fig.tight_layout()
fig.savefig(d / "m1_cycles_vs_P.png", dpi=120)
