#!/usr/bin/env python3
"""Post-m1 deviations and post-hoc evidence -> results/deviations.json (read by make_macros.py).
Values that exist only in prose (D19/D20 before-values, the pre-registered bias list of DESIGN 7.4, the red-team T6.3
probe counts) are recorded below with their source, and each is asserted to appear in that source text (or to match the
probe logs when present). After-values and everything computed come from results/. Derived file, not a measurement."""
import csv, json, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
R = ROOT / "results"
M = ["mnist_cnn", "cifar_mobilenet"]
DEC = (ROOT / "docs/DECISIONS.md").read_text()
load = lambda p: json.loads((R / p).read_text())
fmt = lambda n: f"{n:,}"
need = lambda s, text, what: (s in text) or (_ for _ in ()).throw(AssertionError(f"{what}: {s!r} not in source text"))

# ---- D19 (docs/DECISIONS.md D19, "Before / after"): before-values only exist there
d19_before = {"baseline": {"mnist_cnn": 12188028, "cifar_mobilenet": 72157425},
              "accel_P8": {"mnist_cnn": 451480, "cifar_mobilenet": 197617}}
for k in d19_before.values():
    for v in k.values():
        need(fmt(v), DEC, "D19")
after = {"baseline": {m: load(f"baseline_{m}.json")["mcugrad_result"]["cycles_median"] for m in M},
         "accel_P8": {m: load(f"m5_accel_{m}_P8.json")["mcugrad_result"]["cycles_median"] for m in M}}
for k in after.values():
    for v in k.values():
        need(fmt(v), DEC, "D19 after (results/ must still match the recorded value)")
d19 = {"source": "docs/DECISIONS.md D19 (before); results/baseline_*.json, m5_accel_*_P8.json (after)",
       "before": d19_before, "after": after,
       "speedup_before": {m: d19_before["baseline"][m] / d19_before["accel_P8"][m] for m in M},
       "speedup_after": {m: after["baseline"][m] / after["accel_P8"][m] for m in M}}

# ---- D20: P=8 smoke runs on the pre-fix RTL (D20 text); after = results/m6_c4b.json
d20_before = {"mnist_cnn": {"pred": 18736, "meas": 18928}, "cifar_mobilenet": {"pred": 135876, "meas": 135281}}
for v in d20_before.values():
    for x in v.values():
        need(fmt(x), DEC, "D20")
c4b = load("m6_c4b.json")
err = lambda d: 100.0 * (d["pred"] - d["meas"]) / d["meas"]
d20 = {"source": "docs/DECISIONS.md D20 (before, uncommitted pre-fix smoke runs); results/m6_c4b.json (after)",
       "before": {m: {**v, "err_pct": err(v)} for m, v in d20_before.items()},
       "after": {m: {"pred": c4b[m]["pred"], "meas": c4b[m]["meas"], "err_pct": err(c4b[m])} for m in M}}

# ---- root cause A: rows over their pre-registered bias (DESIGN 7.4 at tag m1)
design_m1 = subprocess.run(["git", "show", "m1:docs/DESIGN.md"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
bias = {("mnist_cnn", 1, 8): 3.4, ("mnist_cnn", 1, 16): 3.4, ("cifar_mobilenet", 2, 8): 3.0, ("cifar_mobilenet", 2, 16): 3.0,
        ("cifar_mobilenet", 4, 16): 3.0, ("cifar_mobilenet", 0, 16): 1.9, ("cifar_mobilenet", 6, 16): 1.3}
BIAS_DEFAULT, MARGIN = 0.5, 0.5   # "all others < 0.5 %"; MARGIN absorbs the rounding of the registered values
for s in ["mnist op_1 +3.4 %", "cifar op_2 +3.0 %", "cifar op_4 +3.0 %", "cifar op_0 +1.9 %", "cifar op_6 +1.3 %", "all others < 0.5 %"]:
    need(s, design_m1, "DESIGN 7.4 at m1")
exceed = []
for r in csv.DictReader((R / "m6_pred_vs_meas.csv").open()):
    k = (r["model"], int(r["op"]), int(r["P"]))
    b, e = bias.get(k, BIAS_DEFAULT), float(r["err_pct"])
    if e > b + MARGIN:
        exceed.append({"model": r["model"], "op": int(r["op"]), "cmd": int(r["cmd"]), "P": int(r["P"]), "pred": int(r["pred_cycles"]),
                       "meas": int(r["meas_cycles"]), "err_pct": e, "registered_bias_pct": b})
a = {"source": "results/m6_pred_vs_meas.csv vs DESIGN.md 7.4 at tag m1 (registered over-prediction by command)",
     "bias_default_pct": BIAS_DEFAULT, "margin_pp": MARGIN,
     "rule": f"signed error > registered bias + {MARGIN} pp (unlisted commands: {BIAS_DEFAULT} %)", "exceeding_rows": exceed}
# PSETUP probe: pixels where a write of the previous tile is granted in PSETUP. First found by red-team T6.3 (scratch
# build/redteam_A) on two rows; all six rows re-measured by experiments/probe_tie.py -> results/m6_tie_probe.json.
tie = {"source": "results/m6_tie_probe.json (experiments/probe_tie.py); first found by red-team T6.3, build/redteam_A",
       "mnist_cnn_op1_P8": {"pixels": 784, "writes_in_psetup": 546, "R": 6, "W": 2, "NV": 8, "bus_stamp": 6356},
       "cifar_mobilenet_op0_P16": {"pixels": 1024, "writes_in_psetup": 690, "R": 12, "W": 4, "NV": 16, "bus_stamp": 16616}}
for r in load("m6_tie_probe.json")["rows"]:
    key = f"{r['model']}_op{r['op']}_P{r['P']}"
    t = tie.setdefault(key, {})
    if t:   # the two T6.3 rows: the re-measurement must reproduce them
        assert (t["pixels"], t["writes_in_psetup"], t["bus_stamp"]) == \
            (r["model_terms"]["n_pix"], r["writes_in_psetup"], r["bus_stamp"]), (key, t)
    t.update(pixels=r["model_terms"]["n_pix"], writes_in_psetup=r["writes_in_psetup"], bus_stamp=r["bus_stamp"],
             tie_writes_in_psetup=r["tie_explained"], deviation=r["deviation"], deviation_parts=r["deviation_parts"],
             err_pct_without_tie=r["err_pct_without_tie"], verdict_tie=r["verdict_tie"],
             verdict_psetup_writes=r["verdict_psetup_writes"])
assert {f"{e['model']}_op{e['op']}_P{e['P']}" for e in exceed} <= set(tie) - {"source"}, "exceeding row not probed"
a["tie_probe"] = tie

# ---- root cause B and the independent STAMP check
sm = load("m6_summary.json")["cycles_identity_per_chain"]
b = {"source": "results/m6_summary.json cycles_identity_per_chain (experiments/compare_m1.py)",
     "chain_calls": sum(c["chain_calls"] for c in sm), "plus2_holds": sum(c["identity_holds"] for c in sm),
     "plus1_holds": sum(c["identity_plus1_holds"] for c in sm)}
meas = {(r["model"], int(r["op"]), int(r["P"])): int(r["meas_cycles"]) for r in csv.DictReader((R / "m6_pred_vs_meas.csv").open())}
indep = [("mnist_cnn", 1, 8, 6356), ("mnist_cnn", 3, 8, 7715), ("cifar_mobilenet", 0, 16, 16616)]
for m, op, P, bus in indep:
    assert meas[m, op, P] == bus, (m, op, P, meas[m, op, P], bus)
c = {"source": "red-team T6.3 independent bus-level count (first DESC request to STAMP request); STAMP from results/m6_pred_vs_meas.csv",
     "commands": [{"model": m, "op": op, "P": P, "bus_count": bus, "stamp": meas[m, op, P]} for m, op, P, bus in indep]}

(R / "deviations.json").write_text(json.dumps({"generated_by": "experiments/make_deviations.py", "D19": d19, "D20": d20,
                                               "root_cause_A": a, "root_cause_B": b, "independent_stamp": c}, indent=2) + "\n")
print(f"results/deviations.json: {len(exceed)} rows over registered bias; identity +1 holds {b['plus1_holds']}/{b['chain_calls']}")
