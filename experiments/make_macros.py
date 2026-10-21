#!/usr/bin/env python3
"""results/ -> paper/numbers.tex (+ paper/tab_*.tex). Every paper number comes through here.

Real where a result file exists, \\todo{MEASURE: ...} otherwise; rerun after each milestone.
Pre-registered inputs (predictions) are read from git tag m1, not the working tree.
Expected files for results that do not exist yet (schemas are assumptions; a mismatch yields
a \\todo, never a wrong number):
  results/m3_summary.json         layers[P].rows / .mismatch_bytes, Ps (full M3 campaign, every P)  [C2a]
  results/m5_bitexact.json        c2b_images,c2b_mismatch,c2c_images,c2c_mismatch          [C2b/c]
  results/m5_accel_<model>_P<P>.json  same schema as baseline_<model>.json (mcugrad_result.cycles_median)  [C1, C1b, C5]
  results/m6_pred_vs_meas.csv     columns model,op,cmd,P,pred_cycles,meas_cycles           [C4a]
  results/m6_c4b.json             {"<model>": {"pred": int, "meas": int}} at P=8            [C4b]
  results/syn/<design>_P<P>.jsonl rows from syn/extract.py; rows with a "note" (stub) are ignored [C3]
"""
import csv, hashlib, io, json, statistics, subprocess, sys
from pathlib import Path
from status import c2c, syn_row, syn_rows  # shared rules: C2c verdict, newest-RTL-version synthesis rows

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

# ---- C2 (C2a: the full M3 campaign in results/m3_summary.json, every P; same source as status.py)
m3s = load(R / "m3_summary.json")
if m3s and m3s.get("layers"):
    Ls = m3s["layers"]
    mm = sum(v["mismatch_bytes"] for v in Ls.values())
    emit("MeasCtwoALayers", i(sum(v["rows"] for v in Ls.values())), "results/m3_summary.json sum layers[P].rows")
    emit("MeasCtwoAMismatch", i(mm), "results/m3_summary.json sum layers[P].mismatch_bytes")
    emit("MeasCtwoAPs", ", ".join(str(P) for P in m3s["Ps"]), "results/m3_summary.json Ps (widths of the C2a campaign)")
    verdict("VerdictCtwoA", mm == 0 and all(v["rows"] >= 1000 for v in Ls.values()), "", "C2a (results/m3_summary.json)")
else:
    todo("MeasCtwoALayers", "C2a layers run", "results/m3_summary.json")
    todo("MeasCtwoAMismatch", "C2a mismatching bytes", "results/m3_summary.json")
    todo("MeasCtwoAPs", "C2a widths", "results/m3_summary.json")
    todo("VerdictCtwoA", "C2a verdict", "results/m3_summary.json")
bx = load(R / "m5_bitexact.json") or {}
for k, nm in [("c2b_images", "MeasCtwoBImages"), ("c2b_mismatch", "MeasCtwoBMismatch"),
              ("c2c_images", "MeasCtwoCImages"), ("c2c_mismatch", "MeasCtwoCMismatch")]:
    if k in bx:
        emit(nm, i(bx[k]), f"results/m5_bitexact.json {k}")
    else:
        todo(nm, k, "results/m5_bitexact.json")
for m, M in MODELS.items():  # C2c second reference: the baseline firmware's outputs (SPEC C2c)
    v = bx.get("per_model_c2c", {}).get(m, {}).get("vs_baseline_firmware")
    if v:
        emit(f"MeasCtwoCBase{M}", i(v["mismatch_bytes"]), f"results/m5_bitexact.json per_model_c2c.{m}.vs_baseline_firmware.mismatch_bytes")
    else:
        todo(f"MeasCtwoCBase{M}", f"C2c vs baseline firmware {m}", "results/m5_bitexact.json")
verdict("VerdictCtwoC", c2c(bx)[0], "C2c verdict", "C2c: mismatch 0 vs run_int8 and vs baseline firmware, both networks (status.c2c)")

# ---- C3 / synthesis
def synrows(design, P=None):
    """Newest RTL version, one row per seed (rule documented in status.syn_rows)."""
    return syn_rows(design, P), (f"{design}_P{P}.jsonl" if P else f"{design}.jsonl")


def syn(prefix, design, P=None):
    rs, fn = synrows(design, P)
    r1 = next((r for r in rs if r["seed"] == 1), rs[0] if rs else None)
    for k, nm, fmt in [("lut4", "Lut", i), ("ff", "Ff", i), ("dp16kd", "Bram", i), ("mult18x18d", "Mult", i)]:
        if r1:
            emit(f"{prefix}{nm}", fmt(r1[k]), f"results/syn/{fn} seed 1 .{k}")
        else:
            todo(f"{prefix}{nm}", f"{design} {k}", f"results/syn/{fn}")
    if rs:
        emit(f"{prefix}Fmax", f1(statistics.median(r["fmax_mhz"] for r in rs)),
             f"results/syn/{fn} median fmax_mhz over {len(rs)} distinct seeds, newest RTL version")
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
c5 = load(R / "m6_c5.json")
for nm, m in (("PstarMeasMnist", "mnist_cnn"), ("PstarMeasCifar", "cifar_mobilenet")):
    if c5:
        emit(nm, c5["models"][m]["measured_pstar"], f"results/m6_c5.json models.{m}.measured_pstar (16 = not reached)")
    else:
        todo(nm, "measured saturation width", "results/m6_c5.json (sum STAMP)")

# ---- post-m1 deviations and post-hoc evidence (results/deviations.json, experiments/make_deviations.py)
dv = load(R / "deviations.json")
if dv:
    for m, M in MODELS.items():
        s = "results/deviations.json D19"
        emit(f"DevBaseBefore{M}", i(dv["D19"]["before"]["baseline"][m]), f"{s} before.baseline (docs/DECISIONS.md D19)")
        emit(f"DevAccelBefore{M}", i(dv["D19"]["before"]["accel_P8"][m]), f"{s} before.accel_P8 (docs/DECISIONS.md D19)")
        emit(f"DevSpeedupBefore{M}", f1(dv["D19"]["speedup_before"][m]), f"{s} speedup_before")
        emit(f"DevSpeedupAfter{M}", f1(dv["D19"]["speedup_after"][m]), f"{s} speedup_after")
        emit(f"DevCfourBMeasBefore{M}", i(dv["D20"]["before"][m]["meas"]), "results/deviations.json D20 before.meas (docs/DECISIONS.md D20)")
        emit(f"DevCfourBErrBefore{M}", f"{dv['D20']['before'][m]['err_pct']:+.1f}", "results/deviations.json D20 before.err_pct")
        emit(f"DevCfourBErrAfter{M}", f"{dv['D20']['after'][m]['err_pct']:+.1f}", "results/deviations.json D20 after.err_pct")
    ex = dv["root_cause_A"]["exceeding_rows"]
    emit("NExceed", len(ex), "results/deviations.json root_cause_A.exceeding_rows")
    emit("BiasDefault", dv["root_cause_A"]["bias_default_pct"], "results/deviations.json root_cause_A.bias_default_pct (DESIGN 7.4 'all others < 0.5 %')")
    emit("ExceedMargin", dv["root_cause_A"]["margin_pp"], "results/deviations.json root_cause_A.margin_pp")
    tp = dv["root_cause_A"]["tie_probe"]
    for k, nm in (("mnist_cnn_op1_P8", "Mnist"), ("cifar_mobilenet_op0_P16", "Cifar")):
        emit(f"Tie{nm}Writes", i(tp[k]["writes_in_psetup"]), f"results/deviations.json root_cause_A.tie_probe.{k}")
        emit(f"Tie{nm}Pixels", i(tp[k]["pixels"]), f"results/deviations.json root_cause_A.tie_probe.{k}")
    b = dv["root_cause_B"]
    emit("IdentCalls", b["chain_calls"], "results/deviations.json root_cause_B.chain_calls")
    emit("IdentPlusOne", b["plus1_holds"], "results/deviations.json root_cause_B.plus1_holds")
    emit("IdentPlusTwo", b["plus2_holds"], "results/deviations.json root_cause_B.plus2_holds")
    ind = dv["independent_stamp"]["commands"]
    emit("IndepStampN", len(ind), "results/deviations.json independent_stamp.commands")
    emit("IndepStampAgree", sum(c["bus_count"] == c["stamp"] for c in ind), "bus-level count == STAMP")
    emit("IndepStampList", ", ".join(i(c["stamp"]) for c in ind), "results/deviations.json independent_stamp.commands[].stamp")
    # post-hoc rescoring: frozen C4b prediction (accelerator + driver) + measured post-D19 fallback-op cycles + bench window
    for m, M in MODELS.items():
        pj = load(R / f"m5_profile_{m}_P8.json")
        fb = statistics.mean(pj["c4b"]["fallback_sum_per_image_run0"])
        assert pj["c4b"]["bench_window_cycles"] == ov["cycles_median"], "bench window differs between profile and baseline_overhead"
        ph = c4b[m]["pred"] + fb + ov["cycles_median"] if m in c4b else None
        s = f"m6_c4b.json {m}.pred + mean(m5_profile_{m}_P8.json c4b.fallback_sum_per_image_run0) + baseline_overhead.json"
        if ph is not None:
            emit(f"PostHocFallback{M}", i(fb), f"mean fallback-op cycles per image, m5_profile_{m}_P8.json c4b.fallback_sum_per_image_run0")
            emit(f"PostHocCycles{M}", i(ph), s)
            emit(f"PostHocSpeedup{M}", f1(base[m] / ph), f"baseline_{m}.json cycles_median / ({s})")
    with open(ROOT / "paper/tab_exceed.tex", "w") as t:
        t.write("% generated by experiments/make_macros.py from results/deviations.json (root_cause_A); do not edit\n")
        t.write("\\begin{tabular}{lrrrrrr}\\toprule\nnet & op & $P$ & pred. & meas. & err.\\,\\% & reg.\\,\\%\\\\\\midrule\n")
        for r in ex:
            t.write(f"{'mnist' if r['model'] == 'mnist_cnn' else 'cifar'} & {r['op']} & {r['P']} & {r['pred']:,} & {r['meas']:,} & "
                    f"{r['err_pct']:.1f} & {r['registered_bias_pct']:.1f} \\\\\n")
        t.write("\\bottomrule\n\\end{tabular}\n")
else:
    for n_ in ("NExceed", "IdentCalls", "IdentPlusOne", "IdentPlusTwo", "IndepStampN", "IndepStampAgree", "IndepStampList"):
        todo(n_, n_, "results/deviations.json (experiments/make_deviations.py)")

# ---- journal extension (paper/journal/main.tex): M3 totals, per-P C4a, C5 sums, Amdahl shares, model archs
emit("EstLutPeight", i(3800), "docs/DESIGN.md 8.12 pre-RTL LUT4 estimate at P=8 (spec constant)")
m3 = load(R / "m3_summary.json")
if m3:
    L, CE = m3["layers"].values(), m3["chains_errors"].values()
    emit("MThreePerP", i(next(iter(m3["layers"].values()))["rows"]), "results/m3_summary.json layers[P].rows")
    for k, nm in (("chains", "Chains"), ("errors", "Errors"), ("start_while_busy", "Busy")):
        emit(f"MThree{nm}Total", i(sum(v[k]["runs"] for v in CE)), f"results/m3_summary.json sum chains_errors[P].{k}.runs")
    emit("MThreeProblemsTotal", i(sum(v["problems"] for v in L) + sum(v[k]["problems"] for v in CE for k in v)
                                  + len(m3["whole_network"]["problems"])), "results/m3_summary.json all problems")
    for m, M in MODELS.items():
        emit(f"MThreeNodes{M}", i(int(m3["whole_network"]["report"][m].split(",")[1].split()[0])),
             f"results/m3_summary.json whole_network.report.{m} accelerated node executions")
    emit("MThreeCommit", m3["commit"][:7], "results/m3_summary.json commit")
m6 = load(R / "m6_summary.json")
if m6:
    for P in PW:
        d = m6["c4a"]["by_P"][str(P)]
        emit(f"CfourMaxErrP{PW[P]}", f1(d["max_abs_err_pct"]), f"results/m6_summary.json c4a.by_P.{P}.max_abs_err_pct")
        emit(f"CfourMedErrP{PW[P]}", f"{d['median_signed_err_pct']:.2f}", f"results/m6_summary.json c4a.by_P.{P}.median_signed_err_pct")
    for m, M in MODELS.items():
        a = m6["amdahl_P8"][m]
        emit(f"AmdahlChainShare{M}", f1(100 * a["chains_share"]), f"results/m6_summary.json amdahl_P8.{m}.chains_share (pct)")
        emit(f"AmdahlFallbackShare{M}", f1(100 * a["fallback_share"]), f"results/m6_summary.json amdahl_P8.{m}.fallback_share (pct)")
        emit(f"AmdahlOther{M}", i(a["other_cycles_mean_interval_minus_ops"]), f"results/m6_summary.json amdahl_P8.{m}.other_cycles_mean_interval_minus_ops")
        emit(f"AmdahlCeiling{M}", f1(base[m] / a["fallback_cycles_mean"]),
             f"baseline_{m}.json cycles_median / m6_summary.json amdahl_P8.{m}.fallback_cycles_mean (speedup with zero-cycle accelerator)")
if c5:
    with open(ROOT / "paper/tab_amdahl.tex", "w") as t:
        t.write("% generated by experiments/make_macros.py from results/m6_c5.json; do not edit\n")
        t.write("\\begin{tabular}{rrrrrrr}\\toprule\n& \\multicolumn{3}{c}{mnist} & \\multicolumn{3}{c}{cifar}\\\\\n"
                "$P$ & run & $\\Sigma$STAMP & acc.\\,\\% & run & $\\Sigma$STAMP & acc.\\,\\%\\\\\\midrule\n")
        for P in PW:
            cells = []
            for m in MODELS:
                d = c5["models"][m]
                run, st = d["measured_model_run_cycles"][str(P)], d["measured_sum_stamp"][str(P)]
                cells += [i(run), i(st), f1(100 * st / run)]
            t.write(f"{P} & " + " & ".join(cells) + " \\\\\n")
        t.write("\\bottomrule\n\\end{tabular}\n")
    for m, M in MODELS.items():
        d = c5["models"][m]
        for P in PW:
            emit(f"MeasStamp{M}P{PW[P]}", i(d["measured_sum_stamp"][str(P)]), f"results/m6_c5.json models.{m}.measured_sum_stamp.{P}")
        emit(f"MeasStampRatio{M}", f"{d['sum_stamp_ratio_to_P16']['8']:.2f}", f"results/m6_c5.json models.{m}.sum_stamp_ratio_to_P16.8")
        emit(f"PstarEndMeas{M}", d["measured_pstar_end_to_end"], f"results/m6_c5.json models.{m}.measured_pstar_end_to_end")
        emit(f"AccShareMeas{M}Peight", f1(100 * d["measured_sum_stamp"]["8"] / d["measured_model_run_cycles"]["8"]),
             f"results/m6_c5.json models.{m} measured_sum_stamp.8 / measured_model_run_cycles.8 (pct)")
for m, M in MODELS.items():  # modelled-only alternative architectures (DESIGN 9.3, 9.4), frozen at m1
    for arch, nm in (("lb", "Lb"), ("acc", "Acc")):
        emit(f"PredStamp{nm}{M}Peight", i(summ["models"][m]["archs"][arch]["P"]["8"]["sum_stamp"]),
             f"m1_summary.json models.{m}.archs.{arch}.P.8.sum_stamp")
_, a1 = syn_row("accel", 1)
_, a16 = syn_row("accel", 16)
_, rp = syn_row("picorv32")
if r8 and rp:
    emit("AccelOverCpuLut", f1(r8["lut4"] / rp["lut4"]), "accel_P8 seed 1 lut4 / picorv32 seed 1 lut4")
    emit("SynLutOverBudget", f1(100 * (r8["lut4"] - 6000) / 6000), "(accel_P8 seed 1 lut4 - 6000) / 6000 (pct)")
if a1 and a16:
    emit("SynLutPerP", i((a16["lut4"] - a1["lut4"]) / 15), "(accel_P16 - accel_P1) seed 1 lut4 / 15")
sn, sp = synrows("soc_noaccel")[0], synrows("soc", 8)[0]
if sn and sp:
    ratio = statistics.median(r["fmax_mhz"] for r in sp) / statistics.median(r["fmax_mhz"] for r in sn)
    emit("FmaxRatioSoc", f"{ratio:.2f}", "median fmax soc_P8 / median fmax soc_noaccel (newest RTL version)")
    for m, M in MODELS.items():
        if (m, 8) in meas:
            emit(f"WallSpeedupEst{M}", f1(base[m] / meas[m, 8] * ratio),
                 f"MeasSpeedup P=8 x FmaxRatioSoc (estimate: each SoC at its own median Fmax; not a board measurement)")
with open(ROOT / "paper/tab_ops.tex", "w") as t:  # per-op Amdahl decomposition at P=8 (profile builds)
    t.write("% generated by experiments/make_macros.py from results/baseline_profile_*.json and m5_profile_*_P8.json; do not edit\n")
    t.write("\\begin{tabular}{llrr}\\toprule\nnet & op (kind) & baseline & accel.\\ $P{=}8$\\\\\\midrule\n")
    for m in MODELS:
        bo = {o["op"]: o for o in load(R / f"baseline_profile_{m}.json")["ops"]}
        for o in load(R / f"m5_profile_{m}_P8.json")["accelerated_ops"]:
            if o["name"].startswith("chain_"):
                _, _, lo, hi = o["name"].split("_")
                ops = [k for k in bo if int(lo) <= k <= int(hi)]
                lab = f"{lo}" if lo == hi else f"{lo}--{hi}"
                kinds = sorted({bo[k]["name"].replace("_fused", "").replace("2d", "") for k in ops})
                lab += f" ({'/'.join(kinds)}, chain)"
                bc = sum(bo[k]["cycles_per_inference"] for k in ops)
            else:
                lab, bc = f"{o['op']} ({o['name'].replace('2d', '')})", bo[o["op"]]["cycles_per_inference"]
            t.write(f"{'mnist' if m == 'mnist_cnn' else 'cifar'} & {lab} & {i(bc)} & {i(o['cycles_per_inference'])} \\\\\n")
        if m == "mnist_cnn":
            t.write("\\midrule\n")
    t.write("\\bottomrule\n\\end{tabular}\n")

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
