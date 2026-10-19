#!/usr/bin/env python3
"""M3 campaign: C2a (1000 random layers), chains/errors, C2b (whole networks, layer by layer).

Writes results/m3_bitexact.csv (C2a: P=8, seeds 0..999, columns seed,mismatch_bytes,...),
results/m3_bitexact_allP.csv (same seeds at P in 1,2,4,8,16), results/m3_summary.json (counts,
provenance), results/m5_bitexact.json c2b_* keys (c2c_* keys are preserved).
Jobs run in at most 4 worker threads (each sim is its own subprocess), each with its own output dir under build/m3/.
Usage: python experiments/run_m3.py [--quick]   (quick = 40 seeds, 2 images, for plumbing)
"""
import argparse, copy, csv, json, os, re, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tb"))
PS = [1, 2, 4, 8, 16]
RUNNERS = {}
SHARDS = 4
WORK = ROOT / "build" / "m3"


def sim(module, P, job, env, testcase=None):
    """Like harness.run_sim, but with a private results xml and out dir (parallel safe)."""
    from accel import harness as H
    from cocotb_tools.runner import get_runner
    out = WORK / job
    out.mkdir(parents=True, exist_ok=True)
    build = ROOT / "build" / f"accel_P{P}"
    r = copy.deepcopy(RUNNERS[P])        # per-job copy: a shared runner cross-wired concurrent jobs; built once by prebuild(); rebuilding inside parallel jobs races
    t = time.time()
    try:
        r.test(hdl_toplevel="mcuaccel_top", test_module=module, test_dir=str(ROOT / "tb/accel"),
               build_dir=build / "sim", results_xml=str(out / "results.xml"), testcase=testcase,
               extra_env={"MCUACCEL_OUT": str(out), **env})
        err = ""
    except BaseException as e:           # cocotb failure / crash: the artefacts still tell what happened
        err = repr(e)[:300]
    return {"job": job, "err": err, "secs": round(time.time() - t, 1)}


def job_layers(P, shard, lo, n):
    return sim("test_random_layers", P, f"layers_P{P}_s{shard}",
               {"MCUACCEL_SEED0": str(lo), "MCUACCEL_SEEDS": str(n), "MCUACCEL_EXEC_STATS": "0",
                "MCUACCEL_CSV": str(WORK / f"layers_P{P}_s{shard}" / "rows.csv")}, "random_layers")


def job_chains(P, nch, nerr):
    return sim("test_chains_errors", P, f"chains_P{P}",
               {"MCUACCEL_CHAINS": str(nch), "MCUACCEL_ERR_CHAINS": str(nerr)})


def job_net(P, full):
    return sim("test_whole_network", P, f"net_P{P}",
               {"MCUACCEL_FULL": "1" if full else "0", "MCUACCEL_MODELS": "mnist_cnn,cifar_mobilenet"},
               "whole_network")


def prebuild(P):
    from accel import harness as H  # noqa: F401
    from cocotb_tools.runner import get_runner
    build = ROOT / "build" / f"accel_P{P}"
    RUNNERS[P] = r = get_runner("verilator")
    r.build(sources=[str(s) for s in sorted((ROOT / "rtl/accel").glob("*.sv"))], hdl_toplevel="mcuaccel_top",
            build_dir=build / "sim", parameters={"P": P}, always=False,
            build_args=["-Wno-fatal", "--timing", str(ROOT / "syn/lint.vlt")])


def jload(p):
    p = Path(p)
    return json.loads(p.read_text()) if p.exists() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    N = 40 if a.quick else 1000
    nch, nerr = (6, 2) if a.quick else (100, 10)
    t0 = time.time()
    commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "rtl/accel"],
                                capture_output=True, text=True).stdout.strip())
    ver = subprocess.run(["verilator", "--version"], capture_output=True, text=True).stdout.strip()
    import cocotb
    import shutil
    shutil.rmtree(WORK, ignore_errors=True)      # per-job CSVs append: start clean
    WORK.mkdir(parents=True, exist_ok=True)
    for P in PS:
        prebuild(P)
    step = N // SHARDS
    jobs = []
    with ThreadPoolExecutor(4) as ex:
        for P in PS:
            for s in range(SHARDS):
                jobs.append(ex.submit(job_layers, P, s, s * step, step))
            jobs.append(ex.submit(job_chains, P, nch, nerr))
        jobs.append(ex.submit(job_net, 8, not a.quick))
        runs = [j.result() for j in jobs]
    # ---- C2a rows
    rows, summary = [], {"layers": {}, "chains_errors": {}}
    for P in PS:
        probs, nrow, mm = [], 0, 0
        for s in range(SHARDS):
            d = WORK / f"layers_P{P}_s{s}"
            sj = jload(d / "random_layers.json")
            pr = (sj or {}).get("problems", [])
            probs += pr
            bad_seeds = {}
            for m in pr:
                g = re.match(r"seed (\d+)", m)
                if g:
                    bad_seeds.setdefault(int(g.group(1)), []).append(m)
            f = d / "rows.csv"
            if f.exists():
                for r in csv.DictReader(open(f)):
                    r["mismatch_bytes"] = r.pop("mismatches")
                    r["problems"] = len(bad_seeds.get(int(r["seed"]), []))
                    rows.append(r)
                    nrow += 1
                    mm += int(r["mismatch_bytes"])
        summary["layers"][P] = {"rows": nrow, "mismatch_bytes": mm, "problems": len(probs),
                                "first_problems": probs[:5]}
        ce = {}
        for nm in ("chains", "errors", "start_while_busy"):
            sj = jload(WORK / f"chains_P{P}" / f"{nm}.json")
            ce[nm] = None if sj is None else {"runs": sj["runs"], "problems": len(sj["problems"]),
                                              "first_problems": sj["problems"][:5]}
        summary["chains_errors"][P] = ce
    cols = ["seed", "mismatch_bytes", "problems", "P", "mode", "H", "W", "C", "O", "KH", "KW", "SH",
            "SW", "PH", "PW", "x_align", "y_align", "stalls", "cycles", "stamps", "commit", "date"]
    R = ROOT / "results"
    for name, sel in (("m3_bitexact.csv", lambda r: r["P"] == "8"), ("m3_bitexact_allP.csv", lambda r: True)):
        with open(R / name, "w", newline="") as f:
            w = csv.DictWriter(f, cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(sorted((r for r in rows if sel(r)), key=lambda r: (int(r["P"]), int(r["seed"]))))
    # ---- C2b
    rep = jload(WORK / "net_P8" / "whole_network_report.json")
    nsum = jload(WORK / "net_P8" / "whole_network.json")
    bx = jload(R / "m5_bitexact.json") or {}
    if rep and nsum:
        imgs = rep.get("_images", {})
        bx.update({"c2b_images": min(imgs.values()) if imgs else 0, "c2b_mismatch": rep["_mismatch_bytes"],
                   "c2b_images_per_model": imgs, "c2b_problems": len(nsum["problems"]), "c2b_P": 8})
        (R / "m5_bitexact.json").write_text(json.dumps(bx, indent=1) + "\n")
    summary.update({
        "commit": commit, "rtl_accel_dirty": dirty, "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "verilator": ver, "cocotb": cocotb.__version__, "seeds": [0, N - 1], "Ps": PS,
        "chains_per_P": nch, "error_chains_per_P": nerr, "quick": a.quick,
        "whole_network": {"report": rep, "problems": (nsum or {}).get("problems", [])[:10]},
        "job_errors": [r for r in runs if r["err"]], "job_secs": {r["job"]: r["secs"] for r in runs},
        "wall_secs": round(time.time() - t0, 1)})
    (R / "m3_summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: summary[k] for k in ("layers", "chains_errors", "job_errors", "wall_secs")}, indent=1))


if __name__ == "__main__":
    main()
