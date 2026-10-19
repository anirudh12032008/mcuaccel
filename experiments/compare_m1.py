#!/usr/bin/env python3
"""M6 / T6.2: measured STAMPs vs the frozen M1 predictions (C4a), per-chain CYCLES identity (frozen +2 and BUSY-high +1), C5, Amdahl, variance.
Inputs : results/m6_stamps_<model>_P<P>.json (run_soc.py --stamps), results/m5_accel_*.json, results/m5_profile_*_P8.json,
         results/baseline_*.json, and `git show m1:results/m1_predictions.csv|m1_summary.json` (frozen; never the working copy).
Outputs: results/m6_pred_vs_meas.csv (model,op,cmd,P,pred_cycles,meas_cycles,err_pct,meas_min,meas_max,kind,commit,tree_dirty)
         results/m6_c5.json, results/m6_summary.json.
Join key (SPEC C4a): (arch "os", model, op, P, split index = cmd). meas = median over the 10 timed images of the command's STAMP."""
import csv, io, json, os, statistics, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = os.path.join(ROOT, "results")
MODELS, PS, LAM = ["mnist_cnn", "cifar_mobilenet"], [1, 2, 4, 8, 16], 1


def git(*a):
    r = subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, check=True)
    return r.stdout


def load(n):
    return json.load(open(os.path.join(R, n)))


pred_txt = git("show", "m1:results/m1_predictions.csv")
summ = json.loads(git("show", "m1:results/m1_summary.json"))
pred = {(r["model"], int(r["op"]), int(r["cmd"]), int(r["P"])): r for r in csv.DictReader(io.StringIO(pred_txt)) if r["arch"] == "os"}

rows, viol_missing, chain_chk, sum_stamp, stamp_spread = [], [], [], {}, []
seen = set()
for m in MODELS:
    for P in PS:
        j = load(f"m6_stamps_{m}_P{P}.json")
        cmds, per_chain = [tuple(c) for c in j["commands"]], j["commands_per_chain"]
        timed = j["inferences"][1:]                      # index 0 = warm-up
        per_cmd = [[s for ch in inf for s in ch["stamps"]] for inf in timed]
        for ci, (op, k) in enumerate(cmds):
            v = [pc[ci] for pc in per_cmd]
            key = (m, op, k, P)
            seen.add(key)
            meas = statistics.median(v)
            if len(set(v)) > 1:
                stamp_spread.append({"key": list(key), "min": min(v), "max": max(v)})
            if key not in pred:
                viol_missing.append({"side": "measured command without prediction row", "key": list(key), "meas": meas})
                continue
            p = pred[key]
            pc = int(float(p["t_cmd"]))
            rows.append({"model": m, "op": op, "cmd": k, "P": P, "pred_cycles": pc, "meas_cycles": int(meas),
                         "err_pct": round(100.0 * (pc - meas) / meas, 4), "meas_min": min(v), "meas_max": max(v),
                         "kind": p["kind"], "commit": j["commit"], "tree_dirty": j["tree_dirty"]})
        # CYCLES vs sum(STAMP + lam + 1) per chain call, every call of every inference.
        #  frozen check  : + 2 (DESIGN 7.2 T_chain, the CPU-visible term incl. the START acceptance cycle)  -> identity_holds/fails
        #  added (D21)   : + 1 (CYCLES counts BUSY-high cycles; the START acceptance cycle is not BUSY)     -> identity_plus1_*
        n_ok = n_bad = n_ok1 = 0
        bad = []
        for ii, inf in enumerate(j["inferences"]):
            for ci, ch in enumerate(inf):
                base = sum(s + LAM + 1 for s in ch["stamps"])
                want = base + 2
                n_ok1 += ch["cycles"] == base + 1
                if ch["cycles"] == want:
                    n_ok += 1
                else:
                    n_bad += 1
                    bad.append({"inference": ii, "chain": ci, "cycles": ch["cycles"], "want": want, "delta": ch["cycles"] - want})
        chain_chk.append({"model": m, "P": P, "chain_calls": n_ok + n_bad, "identity_holds": n_ok, "identity_fails": n_bad,
                      "identity_plus1_holds": n_ok1, "identity_plus1_fails": n_ok + n_bad - n_ok1,
                      "delta_histogram": {str(d): sum(b["delta"] == d for b in bad) for d in sorted({b["delta"] for b in bad})}, "fails": bad[:5]})
        sum_stamp[m, P] = statistics.median(sum(pc) for pc in per_cmd)
for key, p in pred.items():
    if key not in seen:
        viol_missing.append({"side": "prediction row without measured command", "key": list(key)})

cols = ["model", "op", "cmd", "P", "pred_cycles", "meas_cycles", "err_pct", "meas_min", "meas_max", "kind", "commit", "tree_dirty"]
with open(os.path.join(R, "m6_pred_vs_meas.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, cols)
    w.writeheader()
    w.writerows(rows)

errs = [abs(r["err_pct"]) for r in rows]
by_P = {P: {"rows": sum(r["P"] == P for r in rows), "max_abs_err_pct": round(max(abs(r["err_pct"]) for r in rows if r["P"] == P), 3),
            "median_signed_err_pct": round(statistics.median(r["err_pct"] for r in rows if r["P"] == P), 3)} for P in PS}
worst = sorted(rows, key=lambda r: -abs(r["err_pct"]))[:10]
c4a = {"rows": len(rows), "predicted_rows_os": len(pred), "unmatched": viol_missing, "n_error_gt_20pct": sum(e > 20 for e in errs),
       "violations_total": sum(e > 20 for e in errs) + len(viol_missing), "max_abs_err_pct": round(max(errs), 3),
       "median_signed_err_pct": round(statistics.median(r["err_pct"] for r in rows), 3),
       "by_P": by_P, "worst10": [{k: r[k] for k in ("model", "op", "cmd", "P", "pred_cycles", "meas_cycles", "err_pct")} for r in worst],
       "stamps_varying_across_images": stamp_spread,
       "verdict": "confirmed" if not viol_missing and all(e <= 20 for e in errs) else "refuted"}

# ---- C5: measured sum STAMP per P and P* (smallest P with sum <= 1.10 * sum(P=16)); predicted from the frozen summary
c5 = {"rule": "SPEC C5: P* = smallest P whose sum(STAMP) is within 10% of P=16 (16 = not reached on this grid); descriptive",
      "stamp_source": "median over 10 timed images of the per-inference sum of every command's STAMP (m6_stamps_*.json)", "models": {}}
for m in MODELS:
    ss = {P: sum_stamp[m, P] for P in PS}
    pstar = next(P for P in PS if ss[P] <= 1.10 * ss[16])
    A = summ["models"][m]["archs"]["os"]
    pss = {P: A["P"][str(P)]["sum_stamp"] for P in PS}
    pm = {}
    for P in PS:
        try:
            pm[P] = load(f"m5_accel_{m}_P{P}.json")["mcugrad_result"]["cycles_median"]
        except FileNotFoundError:
            pm[P] = None
    pstar_e2e = next((P for P in PS if pm[P] is not None and all(pm[Q] is not None for Q in PS) and pm[P] <= 1.10 * pm[16]), None)
    c5["models"][m] = {"measured_sum_stamp": ss, "predicted_sum_stamp": pss, "measured_pstar": pstar, "predicted_pstar": A["p_star"],
                       "measured_model_run_cycles": pm, "measured_pstar_end_to_end": pstar_e2e,
                       "predicted_pstar_end_to_end": A["p_star_end_to_end"],
                       "sum_stamp_ratio_to_P16": {P: round(ss[P] / ss[16], 4) for P in PS}}
c5["commit"] = rows[0]["commit"]
json.dump(c5, open(os.path.join(R, "m6_c5.json"), "w"), indent=2)

# ---- Amdahl shares (P=8 profile) and variance
sm = {"c4a": c4a, "cycles_identity_per_chain": chain_chk, "amdahl_P8": {}, "variance": {}, "baseline": {}}
for m in MODELS:
    pj = load(f"m5_profile_{m}_P8.json")
    n_acc = 1 + pj["accelerated_profile_images"] * pj["profile_runs"]
    iv = pj["c4b"]["interval_c1_minus_c0_per_timed_inference"]
    total = statistics.median(iv)
    ops = pj["accelerated_ops"]
    ch = [o for o in ops if o["kernel"] == "mcuaccel_run"]
    fb = [o for o in ops if o["kernel"] != "mcuaccel_run"]
    per = lambda o: o["cycles_total"] / n_acc
    sm["amdahl_P8"][m] = {
        "source": f"results/m5_profile_{m}_P8.json (PROFILE build, instrumentation inside the intervals); per-op mean over warm-up + {n_acc - 1} timed inferences",
        "interval_median_cycles": total, "chains_cycles_mean": sum(per(o) for o in ch), "fallback_cycles_mean": sum(per(o) for o in fb),
        "chains_share": sum(per(o) for o in ch) / (sum(per(o) for o in ch) + sum(per(o) for o in fb)),
        "fallback_share": sum(per(o) for o in fb) / (sum(per(o) for o in ch) + sum(per(o) for o in fb)),
        "other_cycles_mean_interval_minus_ops": statistics.mean(iv) - sum(per(o) for o in ops),
        "ops": [{"op": o["op"], "name": o["name"], "kernel": o["kernel"], "cycles_mean": per(o)} for o in ops],
        "fairness_pass": pj["fairness"]["pass"], "c4b": {k: pj["c4b"][k] for k in ("pred", "meas", "signed_error_pct", "within_10pct")}}
    base = load(f"baseline_{m}.json")
    b = base["mcugrad_result"]
    sm["baseline"][m] = {k: b[k] for k in ("cycles_min", "cycles_mean", "cycles_max", "cycles_median", "cycles_run_medians", "runs", "n_images")}
    sm["variance"][m] = {}
    for P in PS:
        a = load(f"m5_accel_{m}_P{P}.json")
        r = a["mcugrad_result"]
        sm["variance"][m][P] = {k: r[k] for k in ("cycles_min", "cycles_mean", "cycles_max", "cycles_median", "cycles_run_medians")}
        sm["variance"][m][P].update({"speedup_median": b["cycles_median"] / r["cycles_median"],
                                     "speedup_range_per_image_extremes": [b["cycles_min"] / r["cycles_max"], b["cycles_max"] / r["cycles_min"]],
                                     "correct": r["correct"], "n_images": r["n_images"], "commit": a["commit"], "tree_dirty": a["tree_dirty"]})
sm["provenance"] = {"script": "experiments/compare_m1.py", "predictions": "git show m1:results/m1_predictions.csv (arch os)",
                    "m1_commit": git("rev-parse", "m1^{}").strip(), "head": git("rev-parse", "HEAD").strip(),
                    "stamp_runs_tree_dirty": any(r["tree_dirty"] for r in rows)}
json.dump(sm, open(os.path.join(R, "m6_summary.json"), "w"), indent=2)
print(f"C4a rows={len(rows)} (pred rows os={len(pred)}) unmatched={len(viol_missing)} >20%={c4a['n_error_gt_20pct']} max|err|={c4a['max_abs_err_pct']}% verdict={c4a['verdict']}")
for P, d in by_P.items():
    print(f"  P={P}: {d}")
tot = sum(c["chain_calls"] for c in chain_chk)
print(f"CYCLES identity over {tot} chain calls ({len(chain_chk)} model x P files): "
      f"frozen +2 holds {sum(c['identity_holds'] for c in chain_chk)}, +1 (BUSY-high) holds {sum(c['identity_plus1_holds'] for c in chain_chk)}")
for m in MODELS:
    c = c5["models"][m]
    print(f"C5 {m}: measured P*={c['measured_pstar']} predicted P*={c['predicted_pstar']} e2e meas={c['measured_pstar_end_to_end']} pred={c['predicted_pstar_end_to_end']}")
