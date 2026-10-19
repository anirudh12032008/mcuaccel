"""Shared by make_readme_tables.py and make_final_report.py: read results/, compute claim verdicts.
Never invents numbers: anything without a result file is None / 'pending'. Thresholds: docs/SPEC.md section 6."""
import csv, hashlib, io, json, statistics, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
R = ROOT / "results"
MODELS = {"mnist_cnn": "mnist_cnn", "cifar_mobilenet": "cifar_mobilenet"}
PS = [1, 2, 4, 8, 16]


def load(p):
    p = Path(p)
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def git(*a):
    try:
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, check=True).stdout.decode()
    except Exception:
        return None


def jsonl(p):
    p = Path(p)
    if not p.exists():
        return []
    return [r for r in map(json.loads, filter(str.strip, p.read_text().splitlines())) if "note" not in r]


def n(x, d=0):
    return "pending" if x is None else f"{x:,.{d}f}"


def prereg():
    """(commit, sha256) of the pre-registered predictions at tag m1, from `git show m1:` bytes."""
    c = (git("rev-parse", "m1^{}") or "").strip() or None
    raw = subprocess.run(["git", "show", "m1:results/m1_predictions.csv"], cwd=ROOT, capture_output=True)
    return c, (hashlib.sha256(raw.stdout).hexdigest() if raw.returncode == 0 else None)


def baseline(m):
    b = load(R / f"baseline_{m}.json")
    return b["mcugrad_result"]["cycles_median"] if b else None


def accel(m, P):
    j = load(R / f"m5_accel_{m}_P{P}.json")
    return j["mcugrad_result"]["cycles_median"] if j else None


def predicted_speedup(m, P=8):
    s = git("show", "m1:results/m1_summary.json")
    try:
        return json.loads(s)["models"][m]["archs"]["os"]["P"][str(P)]["predicted_speedup"]
    except Exception:
        return None


SYN_SIG = ("lut4", "ff", "dp16kd", "mult18x18d", "lutram")


def posthoc_speedup(m):
    """Post-hoc rescoring (never the scored prediction): baseline / (frozen C4b pred + measured mean fallback-op cycles of the
    accelerated profile build + bench window). Same formula as make_macros.py PostHocSpeedup*."""
    c4b, pj = load(R / "m6_c4b.json"), load(R / f"m5_profile_{m}_P8.json")
    ov, b = load(R / "baseline_overhead.json"), baseline(m)
    if not (c4b and pj and ov and b and m in c4b):
        return None
    return b / (c4b[m]["pred"] + statistics.mean(pj["c4b"]["fallback_sum_per_image_run0"]) + ov["cycles_median"])


def syn_rows(design, P=None):
    """Synthesis rows of one result file: the newest RTL version only, one row per seed.
    Rows record the git HEAD (commit) of a possibly dirty tree, not an RTL hash, so the RTL version of a row is its
    resource signature (SYN_SIG): rows of the same signature are one version, the version whose newest row has the
    latest `date` is kept (appended reruns after an RTL change supersede older rows; a change that leaves every
    count equal is indistinguishable, and harmless for resources). Within a version a repeated seed keeps its
    latest row. Seed count = len(result)."""
    rs = jsonl(R / "syn" / (f"{design}_P{P}.jsonl" if P else f"{design}.jsonl"))
    if not rs:
        return []
    sig = lambda r: tuple(r.get(k) for k in SYN_SIG)
    newest = sig(max(rs, key=lambda r: r["date"]))
    by_seed = {}
    for r in sorted((r for r in rs if sig(r) == newest), key=lambda r: r["date"]):
        by_seed[r["seed"]] = r
    return [by_seed[s] for s in sorted(by_seed)]


def syn_row(design, P=None):
    rs = syn_rows(design, P)
    return rs, next((r for r in rs if r["seed"] == 1), rs[0] if rs else None)


def c2c(bx):
    """C2c (SPEC): 0 mismatch vs run_int8 AND vs the baseline firmware, both networks, >= 100 images.
    -> (True|False|None, evidence); None = pending (comparison not yet recorded)."""
    if "c2c_mismatch" not in bx:
        return None, "pending"
    pm, base, bad = bx.get("per_model_c2c", {}), {}, bx["c2c_mismatch"] != 0 or bx["c2c_images"] < 100
    for m in MODELS:
        v = pm.get(m, {}).get("vs_baseline_firmware")
        base[m] = None if v is None else v["mismatch_bytes"]
    ev = "vs baseline firmware: " + ", ".join(f"{m} {'pending' if x is None else x}" for m, x in base.items())
    if bad or any(x for x in base.values() if x is not None):
        return False, ev
    return (None if None in base.values() else True), ev


def claims():
    """-> list of (id, statement, verdict, evidence). verdict in confirmed|refuted|pending|descriptive."""
    out = []
    sp = {m: (baseline(m) / accel(m, 8) if baseline(m) and accel(m, 8) else None) for m in MODELS}
    ev = "; ".join(f"{m} {n(s, 1)}x" for m, s in sp.items())
    v = "pending" if None in sp.values() else ("confirmed" if all(s >= 10 for s in sp.values()) else "refuted")
    out.append(("C1", "S(P=8) >= 10 on both networks", v, ev))
    s = sp["cifar_mobilenet"]
    out.append(("C1b", "S(cifar_mobilenet, P=8) >= 100", "pending" if s is None else ("confirmed" if s >= 100 else "refuted"),
                f"cifar_mobilenet {n(s, 1)}x"))
    # C2: a = m3 random layers, b = whole-network layer by layer, c = SoC final outputs
    m3 = load(R / "m3_summary.json")
    a = None
    if m3 and not m3.get("quick"):
        L = m3["layers"]
        full = all(str(P) in L and L[str(P)]["rows"] >= 1000 for P in PS)
        mm = sum(L[k]["mismatch_bytes"] + L[k]["problems"] for k in L)
        a = False if mm else (True if full else None)
        eva = f"{sum(L[k]['rows'] for k in L)} layers over P={','.join(L)}, {mm} mismatching"
    else:
        eva = "results/m3_summary.json missing or quick run"
    bx = load(R / "m5_bitexact.json") or {}
    b = None if "c2b_mismatch" not in bx else (bx["c2b_mismatch"] == 0 and bx.get("c2b_problems", 0) == 0)
    c, evc = c2c(bx)
    parts = [a, b, c]
    v = "refuted" if False in parts else ("confirmed" if all(p for p in parts) else "pending")
    out.append(("C2", "0 mismatching bytes: (a) 1000 random layers per P, (b) 10 images layer by layer, (c) 100 images on the SoC",
                v, f"(a) {eva}; (b) {n(bx.get('c2b_images'))} images, {bx.get('c2b_mismatch', 'pending')} mismatch; "
                   f"(c) {n(bx.get('c2c_images'))} images, {bx.get('c2c_mismatch', 'pending')} mismatch, {evc}"))
    # C3
    rs, r = syn_row("accel", 8)
    if r:
        ok = r["lut4"] <= 6000 and r["mult18x18d"] <= 40 and r["dp16kd"] <= 12
        prov = f", row from {r['commit'][:7]}{' (dirty tree)' if r.get('dirty') else ''}"
        out.append(("C3", "accel P=8 <= 6000 LUT4, 40 MULT18X18D, 12 DP16KD", "confirmed" if ok else "refuted",
                    f"{r['lut4']} LUT4, {r['mult18x18d']} MULT18X18D, {r['dp16kd']} DP16KD{prov}"))
    else:
        out.append(("C3", "accel P=8 <= 6000 LUT4, 40 MULT18X18D, 12 DP16KD", "pending", "results/syn/accel_P8.jsonl missing"))
    # C4
    rows = []
    p = R / "m6_pred_vs_meas.csv"
    if p.exists():
        rows = list(csv.DictReader(p.open()))
    if rows and {"pred_cycles", "meas_cycles"} <= set(rows[0]):
        err = [abs(float(x["pred_cycles"]) - float(x["meas_cycles"])) / float(x["meas_cycles"]) * 100 for x in rows]
        va = "confirmed" if max(err) <= 20 else "refuted"
        eva = f"{len(rows)} rows, max error {max(err):.1f}%, {sum(e > 20 for e in err)} over 20%"
    else:
        va, eva = "pending", "results/m6_pred_vs_meas.csv missing"
    c4b = load(R / "m6_c4b.json")
    if c4b:
        e = {m: (c4b[m]["pred"] - c4b[m]["meas"]) / c4b[m]["meas"] * 100 for m in c4b}
        vb = "confirmed" if all(abs(x) <= 10 for x in e.values()) else "refuted"
        evb = ", ".join(f"{m} {x:+.2f}%" for m, x in e.items())
    else:
        vb, evb = "pending", "results/m6_c4b.json missing"
    v = "refuted" if "refuted" in (va, vb) else ("confirmed" if va == vb == "confirmed" else "pending")
    dv = load(R / "deviations.json")
    if dv:
        eva += f"; {len(dv['root_cause_A']['exceeding_rows'])} rows exceed their pre-registered bias (D21, DESIGN 7.5)"
    out.append(("C4", "m1 predictions within 20% per command at every P (a) and 10% per network at P=8 (b)", v,
                f"(a) {eva}; (b) {evb}"))
    have = [P for P in PS if all(accel(m, P) for m in MODELS)]
    out.append(("C5", "saturation curve over P (descriptive; falsifiable part is C4a at every P)", "descriptive",
                f"{len(have)}/{len(PS)} P points measured end to end ({','.join(map(str, have)) or 'none'})"))
    return out


def wall(paths):
    s = 0.0
    for p in paths:
        j = load(p)
        if j and "wall_s" in j:
            s += j["wall_s"]
    return s or None
