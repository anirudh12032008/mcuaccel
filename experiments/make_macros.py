#!/usr/bin/env python3
"""results/ -> paper/numbers.tex (+ paper/tab_*.tex). Every paper number comes through here.

Real where a result file exists, \\todo{MEASURE: ...} otherwise; rerun after each milestone.
Pre-registered inputs (predictions) are read from git tag m1, not the working tree.
Expected files for results that do not exist yet (schemas are assumptions; a mismatch yields
a \\todo, never a wrong number):
  results/m3_bitexact.csv         columns seed,mismatch_bytes (one row per random layer)   [C2a]
  results/m5_bitexact.json        c2b_images,c2b_mismatch,c2c_images,c2c_mismatch          [C2b/c]
  results/m5_accel_<model>_P<P>.json  same schema as baseline_<model>.json (mcugrad_result.cycles_median)  [C1, C1b, C5]
  results/m6_pred_vs_meas.csv     columns model,op,cmd,P,pred_cycles,meas_cycles           [C4a]
  results/m6_c4b.json             {"<model>": {"pred": int, "meas": int}} at P=8            [C4b]
  results/syn/<design>_P<P>.jsonl rows from syn/extract.py; rows with a "note" (stub) are ignored [C3]
"""
import csv, hashlib, io, json, statistics, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
R = ROOT / "results"
MODELS = {"mnist_cnn": "Mnist", "cifar_mobilenet": "Cifar"}
PW = {1: "one", 2: "two", 4: "four", 8: "eight", 16: "sixteen"}
out, names = [], set()


def clean(s):
    return str(s).replace("_", " ").replace("%", "pct").replace("&", "and").replace("#", "")


def emit(name, val, src):
    assert name.isalpha() and name not in names, name
    names.add(name)
    out.append(f"\\newcommand{{\\{name}}}{{{val}}}% {src}")


def todo(name, what, src):
    emit(name, f"\\todo{{MEASURE: {clean(what)}}}", f"PENDING {src}")


def i(n):
    return f"{int(round(n)):,}"


def f1(x):
    return f"{x:.1f}"


def load(p):
    p = Path(p)
    return json.loads(p.read_text()) if p.exists() else None


def git(*a):
    try:
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, check=True).stdout.decode()
    except Exception:
        return None


def tagged(path):
    """Pre-registered artifact: content at tag m1, else working tree."""
    s = git("show", f"m1:{path}")
    return s if s is not None else (ROOT / path).read_bytes().decode()


# ---- constants fixed by docs/SPEC.md (thresholds are claims, not measurements)
for n, v, s in [("ThreshCone", 10, "C1"), ("ThreshConeB", 100, "C1b"), ("ThreshFourA", "20", "C4a pct"),
                ("ThreshFourB", "10", "C4b pct"), ("BudgetLut", i(6000), "C3"), ("BudgetMult", 40, "C3"),
                ("BudgetBram", 12, "C3"), ("RandomLayers", i(1000), "C2a"), ("ImagesCtwoB", 10, "C2b"),
                ("ImagesCtwoC", 100, "C2c"), ("PDefault", 8, "default P"), ("PMax", 16, "P sweep"), ("Lanes", 4, "DESIGN 5 lanes per output channel"),
                ("DescWords", 18, "DESIGN 6.2 descriptor words"), ("WbufRows", 512, "section 7 weight buffer rows"),
                ("PortBits", 32, "section 2 accelerator RAM port width"), ("RamMiB", 1, "section 7 simulated RAM")]:
    emit(n, v, f"docs/SPEC.md {s} (spec constant)")
for _P, _w in {1: "one", 2: "two", 4: "four", 8: "eight", 16: "sixteen"}.items():
    emit(f"Pv{_w}", _P, "docs/SPEC.md section 2 (P grid value, table row label)")
emit("PGrid", "1, 2, 4, 8, 16", "docs/SPEC.md section 2 (P ablation grid)")

# ---- pre-registration provenance
tag = (git("rev-parse", "m1^{}") or "").strip()
emit("MOneCommit", tag or "unknown", "git rev-parse m1^{}")
emit("MOneCommitShort", tag[:7] or "unknown", "git rev-parse m1^{}")
pred_txt = tagged("results/m1_predictions.csv")
sha = hashlib.sha256(pred_txt.encode()).hexdigest()  # decode/encode is lossless (no newline translation)
emit("MOneCsvSha", sha, "sha256 of results/m1_predictions.csv at tag m1")
emit("MOneCsvShaShort", sha[:16], "sha256 of results/m1_predictions.csv at tag m1")
preds = [r for r in csv.DictReader(io.StringIO(pred_txt, newline="")) if r["arch"] == "os"]  # scored arch (SPEC C4a)
n_all = pred_txt.count("\n") - 1
summ = json.loads(tagged("results/m1_summary.json"))
emit("MOneModelCommit", summ["commit"][:7], "results/m1_summary.json commit")
emit("MOneRowsAll", n_all, "data rows of results/m1_predictions.csv (all archs)")
emit("MOneRows", len(preds), "rows with arch=os in results/m1_predictions.csv")
for P in PW:
    emit(f"MOneRowsP{PW[P]}", sum(r["P"] == str(P) for r in preds), f"rows with arch=os and P={P}")

# ---- measured baselines
drv = load(R / "driver_overhead.json")
ov = load(R / "baseline_overhead.json")
emit("BenchWindow", i(ov["cycles_median"]), "results/baseline_overhead.json cycles_median")
emit("DrvChain", drv["drv_chain"], "results/driver_overhead.json drv_chain")
emit("DrvPatch", drv["drv_patch"], "results/driver_overhead.json drv_patch")
base = {}
for m, M in MODELS.items():
    b = load(R / f"baseline_{m}.json")
    base[m] = c = b["mcugrad_result"]["cycles_median"]
    s = f"results/baseline_{m}.json"
    emit(f"BaselineCycles{M}", i(c), f"{s} mcugrad_result.cycles_median")
    emit(f"BaselineMin{M}", i(b["mcugrad_result"]["cycles_min"]), f"{s} mcugrad_result.cycles_min")
    emit(f"BaselineMax{M}", i(b["mcugrad_result"]["cycles_max"]), f"{s} mcugrad_result.cycles_max")
    emit(f"BaselineMs{M}", f1(c / b["mcugrad_result"]["clock_hz"] * 1e3), f"{s} cycles_median / clock_hz")
    emit(f"BaselineImages{M}", b["images"], f"{s} images")
    emit(f"BaselineRuns{M}", b["runs"], f"{s} runs")
    emit(f"BaselineCorrect{M}", b["mcugrad_result"]["correct"], f"{s} mcugrad_result.correct (of images)")
    emit(f"ClockMHz{M}", i(b["mcugrad_result"]["clock_hz"] / 1e6), f"{s} clock_hz")
    pr = load(R / f"baseline_profile_{m}.json")
    acc = sum(o["share"] for o in pr["ops"] if o["name"].startswith(("conv2d", "matmul")))
    emit(f"ProfileAccelShare{M}", f1(100 * acc), f"sum of ops[].share with name conv2d*/matmul* in baseline_profile_{m}.json (pct)")
    pool = sum(o["share"] for o in pr["ops"] if o["name"] in ("maxpool2d", "avgpool2d", "transpose"))
    emit(f"ProfileSwShare{M}", f1(100 * pool), f"sum of ops[].share, pool/transpose ops, baseline_profile_{m}.json (pct)")

# ---- M1 predictions (pre-registered)
for m, M in MODELS.items():
    A = summ["models"][m]["archs"]["os"]
    for P in PW:
        d, s = A["P"][str(P)], f"m1_summary.json models.{m}.archs.os.P.{P}"
        emit(f"PredSpeedup{M}P{PW[P]}", f1(d["predicted_speedup"]), f"{s}.predicted_speedup")
        emit(f"PredStamp{M}P{PW[P]}", i(d["sum_stamp"]), f"{s}.sum_stamp")
        emit(f"PredModelRun{M}P{PW[P]}", i(d["predicted_model_run"]), f"{s}.predicted_model_run")
    d, s = A["P"]["8"], f"m1_summary.json models.{m}.archs.os.P.8"
    for k, nm in [("accel_cycles", "AccelChains"), ("sw_cycles", "SwCycles"), ("driver_cycles", "Driver"),
                  ("c4b_pred_cycles", "CfourB"), ("n_commands", "Commands"), ("n_chains", "Chains")]:
        emit(f"Pred{nm}{M}Peight", i(d[k]), f"{s}.{k}")
    emit(f"PredUtil{M}Peight", f1(100 * d["util"]), f"{s}.util (pct)")
    emit(f"PredSwShare{M}Peight", f1(100 * d["sw_cycles"] / d["predicted_model_run"]),
         f"{s}.sw_cycles / predicted_model_run (pct)")
    emit(f"PredPstar{M}", A["p_star"], f"m1_summary.json models.{m}.archs.os.p_star (16 = not reached)")
    emit(f"PredPstarEnd{M}", A["p_star_end_to_end"], f"m1_summary.json models.{m}.archs.os.p_star_end_to_end")

# ---- measured accelerated builds (M5/M6) -> C1, C1b, C5
meas = {}
for m, M in MODELS.items():
    for P in PW:
        p = R / f"m5_accel_{m}_P{P}.json"
        j = load(p)
        nm = f"MeasCycles{M}P{PW[P]}"
        if j:
            c = j["mcugrad_result"]["cycles_median"]
            meas[m, P] = c
            emit(nm, i(c), f"{p.name} mcugrad_result.cycles_median")
            emit(f"MeasSpeedup{M}P{PW[P]}", f1(base[m] / c), f"baseline_{m}.json / {p.name}")
        else:
            todo(nm, f"accelerated cycles {m} P={P}", f"results/m5_accel_{m}_P{P}.json")
            todo(f"MeasSpeedup{M}P{PW[P]}", f"speedup {m} P={P}", f"results/m5_accel_{m}_P{P}.json")


def verdict(name, ok, what, src):
    if ok is None:
        todo(name, what, src)
    else:
        emit(name, "confirmed" if ok else "refuted", src)


s8 = [meas.get((m, 8)) for m in MODELS]
verdict("VerdictCone", None if None in s8 else all(base[m] / meas[m, 8] >= 10 for m in MODELS),
        "C1 verdict", "C1 from m5_accel_*_P8.json")
verdict("VerdictConeB", None if s8[1] is None else base["cifar_mobilenet"] / s8[1] >= 100,
        "C1b verdict", "C1b from m5_accel_cifar_mobilenet_P8.json")

# ---- C2
p = R / "m3_bitexact.csv"
rows = list(csv.DictReader(p.open())) if p.exists() else []
if rows and "mismatch_bytes" in rows[0]:
    mm = sum(int(r["mismatch_bytes"]) for r in rows)
    emit("MeasCtwoALayers", i(len(rows)), "rows of results/m3_bitexact.csv")
    emit("MeasCtwoAMismatch", i(mm), "sum of mismatch_bytes, results/m3_bitexact.csv")
    verdict("VerdictCtwoA", mm == 0 and len(rows) >= 1000, "", "C2a")
else:
    todo("MeasCtwoALayers", "C2a layers run", "results/m3_bitexact.csv")
    todo("MeasCtwoAMismatch", "C2a mismatching bytes", "results/m3_bitexact.csv")
    todo("VerdictCtwoA", "C2a verdict", "results/m3_bitexact.csv")
bx = load(R / "m5_bitexact.json") or {}
for k, nm in [("c2b_images", "MeasCtwoBImages"), ("c2b_mismatch", "MeasCtwoBMismatch"),
              ("c2c_images", "MeasCtwoCImages"), ("c2c_mismatch", "MeasCtwoCMismatch")]:
    if k in bx:
        emit(nm, i(bx[k]), f"results/m5_bitexact.json {k}")
    else:
        todo(nm, k, "results/m5_bitexact.json")

# ---- C3 / synthesis
def synrows(design, P=None):
    p = R / "syn" / (f"{design}_P{P}.jsonl" if P else f"{design}.jsonl")
    if not p.exists():
        return [], p.name
    return [r for r in map(json.loads, filter(str.strip, p.read_text().splitlines())) if "note" not in r], p.name


def syn(prefix, design, P=None):
    rs, fn = synrows(design, P)
    r1 = next((r for r in rs if r["seed"] == 1), None)
    for k, nm, fmt in [("lut4", "Lut", i), ("ff", "Ff", i), ("dp16kd", "Bram", i), ("mult18x18d", "Mult", i)]:
        if r1:
            emit(f"{prefix}{nm}", fmt(r1[k]), f"results/syn/{fn} seed 1 .{k}")
        else:
            todo(f"{prefix}{nm}", f"{design} {k}", f"results/syn/{fn}")
    if rs:
        emit(f"{prefix}Fmax", f1(statistics.median(r["fmax_mhz"] for r in rs)),
             f"results/syn/{fn} median fmax_mhz over {len(rs)} seeds")
        emit(f"{prefix}FmaxSeeds", len(rs), f"results/syn/{fn} seeds")
    else:
        todo(f"{prefix}Fmax", f"{design} Fmax", f"results/syn/{fn}")
        todo(f"{prefix}FmaxSeeds", f"{design} seeds", f"results/syn/{fn}")
    return r1


syn("SynPicorv", "picorv32")
syn("SynSocNoAccel", "soc_noaccel")
syn("SynSocPeight", "soc", 8)
for P in PW:
    r = syn(f"SynAccelP{PW[P]}", "accel", P)
    if P == 8:
        r8 = r
verdict("VerdictCthree", None if r8 is None else
        (r8["lut4"] <= 6000 and r8["mult18x18d"] <= 40 and r8["dp16kd"] <= 12), "C3 verdict", "C3 from syn/accel_P8.jsonl")

# ---- C4
p = R / "m6_pred_vs_meas.csv"
rows = list(csv.DictReader(p.open())) if p.exists() else []
if rows and {"pred_cycles", "meas_cycles"} <= set(rows[0]):
    err = [abs(float(r["pred_cycles"]) - float(r["meas_cycles"])) / float(r["meas_cycles"]) * 100 for r in rows]
    sg = [(float(r["pred_cycles"]) - float(r["meas_cycles"])) / float(r["meas_cycles"]) * 100 for r in rows]
    emit("MeasCfourRows", len(rows), "rows of results/m6_pred_vs_meas.csv")
    emit("MeasCfourMaxErr", f1(max(err)), "max |pred-meas|/meas (pct), results/m6_pred_vs_meas.csv")
    emit("MeasCfourMedianErr", f1(statistics.median(sg)), "median signed error (pct), results/m6_pred_vs_meas.csv")
    emit("MeasCfourViolations", sum(e > 20 for e in err), "rows with error > 20 pct")
    verdict("VerdictCfourA", all(e <= 20 for e in err) and len(rows) == len(preds), "", "C4a")
    meas_by = {(r["model"], r["op"], r["cmd"], r["P"]): float(r["meas_cycles"]) for r in rows}
else:
    meas_by = {}
    for n, w in [("MeasCfourRows", "C4a rows"), ("MeasCfourMaxErr", "C4a max error"),
                 ("MeasCfourMedianErr", "C4a median signed error"),
                 ("MeasCfourViolations", "C4a violations"), ("VerdictCfourA", "C4a verdict")]:
        todo(n, w, "results/m6_pred_vs_meas.csv")
c4b = load(R / "m6_c4b.json") or {}
for m, M in MODELS.items():
    if m in c4b:
        e = (c4b[m]["pred"] - c4b[m]["meas"]) / c4b[m]["meas"] * 100
        emit(f"MeasCfourB{M}", i(c4b[m]["meas"]), f"results/m6_c4b.json {m}.meas")
        emit(f"MeasCfourBErr{M}", f1(e), f"results/m6_c4b.json {m} signed error pct")
    else:
        todo(f"MeasCfourB{M}", f"C4b measured {m}", "results/m6_c4b.json")
        todo(f"MeasCfourBErr{M}", f"C4b error {m}", "results/m6_c4b.json")
for nm in ("PstarMeasMnist", "PstarMeasCifar"):
    todo(nm, "measured saturation width", "results/m5_accel_*_P*.json (sum STAMP)")

# ---- tables (generated, no typed numbers)
with open(ROOT / "paper/tab_pred_cmds.tex", "w") as t:
    t.write("% generated by experiments/make_macros.py from results/m1_predictions.csv (tag m1); do not edit\n")
    t.write("\\tabtodo\\begin{tabular}{llllrr}\\toprule\nnet & op & kind & K & pred. & meas.\\\\\\midrule\n")
    for r in preds:
        if r["P"] != "8":
            continue
        key = (r["model"], r["op"], r["cmd"], "8")
        mv = f"{meas_by[key]:,.0f}" if key in meas_by else "\\todo{M}"
        sh = "mnist" if r["model"] == "mnist_cnn" else "cifar"
        t.write(f"{sh} & {r['op']} & {r['kind']} & {r['KH']}x{r['KW']} & {int(float(r['t_cmd'])):,} & {mv} \\\\\n")
    t.write("\\bottomrule\n\\end{tabular}\n")
(ROOT / "paper/numbers.tex").write_text(
    "% GENERATED by experiments/make_macros.py -- do not edit. Source of each macro in its trailing comment.\n"
    + "\n".join(out) + "\n")
real = sum("PENDING" not in l for l in out)
print(f"paper/numbers.tex: {len(out)} macros, {real} real, {len(out) - real} todo", file=sys.stderr)
