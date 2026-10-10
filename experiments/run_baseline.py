#!/usr/bin/env python3
"""Baseline (D6): mcugrad's generated model.c on the PicoRV32 SoC in Verilator.
    run_baseline.py [--model mnist_cnn|cifar_mobilenet ...] [--images 10] [--runs 3]
                    [--prof-images 2 --prof-runs 1 | --no-profile] [--max-cycles N]
Writes results/baseline_<model>.json and results/baseline_profile_<model>.json."""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "sw"))
import build_fw  # noqa: E402

SIM = os.path.join(ROOT, "build", "sim", "Vsoc_top")
PICORV32_CFG = ("ENABLE_COUNTERS=1 ENABLE_COUNTERS64=1 ENABLE_REGS_16_31=1 ENABLE_REGS_DUALPORT=1 "
                "LATCHED_MEM_RDATA=0 TWO_STAGE_SHIFT=1 BARREL_SHIFTER=1 TWO_CYCLE_COMPARE=0 "
                "TWO_CYCLE_ALU=0 COMPRESSED_ISA=0 CATCH_MISALIGN=1 CATCH_ILLINSN=1 ENABLE_PCPI=0 "
                "ENABLE_MUL=0 ENABLE_FAST_MUL=1 ENABLE_DIV=1 ENABLE_IRQ=0 ENABLE_TRACE=0 "
                "PROGADDR_RESET=0x80000000 STACKADDR=0x80100000 (SPEC section 7)")


def git_head() -> str:
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else "unknown (not a git checkout)"


def conv(v: str):
    for t in (int, float):
        try:
            return t(v)
        except ValueError:
            pass
    return v


def run_sim(hexf: str, max_cycles: int) -> dict:
    t0, c0 = time.time(), os.times()
    p = subprocess.run([SIM, f"+ram={hexf}", f"+max_cycles={max_cycles}"], capture_output=True, text=True)
    wall, c1 = time.time() - t0, os.times()
    cpu = c1.children_user - c0.children_user
    log = os.path.splitext(hexf)[0] + ".log"
    open(log, "w").write(p.stdout + p.stderr)
    m = re.search(r"SIM_CYCLES=(\d+)", p.stdout)
    if p.returncode != 0 or not m or "MCUGRAD_DONE" not in p.stdout:
        sys.exit(f"simulation failed (exit {p.returncode}); see {log}")
    return {"stdout": p.stdout, "sim_cycles": int(m.group(1)), "wall_s": wall, "cpu_s": cpu, "log": log}


def meta(b: dict, images: int, runs: int) -> dict:
    return {"commit": git_head(), "date": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "compiler": b["compiler"], "flags": b["flags"], "picorv32_config": PICORV32_CFG,
            "images": images, "runs": runs, "image_bytes": b["image_bytes"], "x_align": b["x_align"]}


def baseline(model: str, images: int, runs: int, max_cycles: int) -> None:
    b = build_fw.build(model, images, runs)
    r = run_sim(b["hex"], max_cycles)
    kv = {k: conv(v) for k, v in re.findall(r"MCUGRAD_RESULT\s+(\w+)=(\S+)", r["stdout"])}
    out = {"model": model, "kind": "baseline (mcugrad model.c unchanged, D6; uninstrumented build)",
           "mcugrad_result": kv, "sim_cycles": r["sim_cycles"], "wall_s": round(r["wall_s"], 2),
           "sim_cpu_s": round(r["cpu_s"], 2), "sim_cycles_per_cpu_s": round(r["sim_cycles"] / r["cpu_s"]), **meta(b, images, runs)}
    path = os.path.join(ROOT, "results", f"baseline_{model}.json")
    json.dump(out, open(path, "w"), indent=2)
    print(f"{model}: cycles_median={kv.get('cycles_median')} accuracy={kv.get('accuracy')} "
          f"sim_cycles={r['sim_cycles']} wall={r['wall_s']:.1f}s -> {path}")


def overhead(images: int, runs: int, max_cycles: int) -> None:
    """Empty model_run: the fixed cost of bench_main's c1-c0 window (two bench_micros + loop)."""
    b = build_fw.build("mnist_cnn", images, runs, stub=True)
    r = run_sim(b["hex"], max_cycles)
    kv = {k: conv(v) for k, v in re.findall(r"MCUGRAD_RESULT\s+(\w+)=(\S+)", r["stdout"])}
    out = {"kind": "empty model_run stub: fixed bench_main window overhead per inference (cycles)",
           "cycles_median": kv["cycles_median"], "cycles_min": kv["cycles_min"], "cycles_max": kv["cycles_max"],
           "sim_cycles": r["sim_cycles"], **meta(b, images, runs)}
    json.dump(out, open(os.path.join(ROOT, "results", "baseline_overhead.json"), "w"), indent=2)
    print(f"window overhead cycles_median={kv['cycles_median']}")


def profile(model: str, images: int, runs: int, max_cycles: int) -> None:
    b = build_fw.build(model, images, runs, profile=True)
    r = run_sim(b["hex"], max_cycles)
    kv = {k: conv(v) for k, v in re.findall(r"MCUGRAD_RESULT\s+(\w+)=(\S+)", r["stdout"])}
    n_inf = 1 + runs * images   # one untimed warm-up + all timed inferences accumulate in prof_cycles
    ops = []
    for m in re.finditer(r"MCUGRAD_PROF op=(\d+) name=(\S+) kernel=(\S+) cycles=(\d+)", r["stdout"]):
        tot = int(m.group(4))
        ops.append({"op": int(m.group(1)), "name": m.group(2), "kernel": m.group(3),
                    "cycles_total": tot, "cycles_per_inference": tot / n_inf})
    if not ops:
        sys.exit("no MCUGRAD_PROF lines in output")
    s = sum(o["cycles_per_inference"] for o in ops)
    for o in ops:
        o["share"] = o["cycles_per_inference"] / s
    out = {"model": model,
           "kind": "PROFILE build (-DMCUACCEL_PROFILE): rdcycle around each op_* call adds a few cycles "
                   "per op; per-op numbers and cycles_median here come from the instrumented build, "
                   "NOT the baseline. Use baseline_<model>.json for the headline number.",
           "inferences_averaged": n_inf, "ops": ops, "sum_op_cycles_per_inference": s,
           "profile_build_cycles_median": kv.get("cycles_median"), "accuracy": kv.get("accuracy"),
           "sim_cycles": r["sim_cycles"], "wall_s": round(r["wall_s"], 2), "sim_cpu_s": round(r["cpu_s"], 2), **meta(b, images, runs)}
    path = os.path.join(ROOT, "results", f"baseline_profile_{model}.json")
    json.dump(out, open(path, "w"), indent=2)
    print(f"{model} profile: sum_ops/inf={s:.0f} median={kv.get('cycles_median')} -> {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", action="append", choices=["mnist_cnn", "cifar_mobilenet"])
    ap.add_argument("--images", type=int, default=10)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--prof-images", type=int, default=2)
    ap.add_argument("--prof-runs", type=int, default=1)
    ap.add_argument("--no-profile", action="store_true")
    ap.add_argument("--max-cycles", type=int, default=20_000_000_000)
    a = ap.parse_args()
    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    overhead(a.images, a.runs, a.max_cycles)
    for m in a.model or ["mnist_cnn", "cifar_mobilenet"]:
        baseline(m, a.images, a.runs, a.max_cycles)
        if not a.no_profile:
            profile(m, min(a.images, a.prof_images), a.prof_runs, a.max_cycles)
