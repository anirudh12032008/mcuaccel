#!/usr/bin/env python3
"""M5 / T5.1: accelerated firmware on the Verilator SoC (C1, C1b, C2c, C4b, C5).
    run_soc.py --model mnist_cnn --P 8 [--P 1 --P 2 ...] [--images 10 --runs 3]      timing  -> results/m5_accel_<model>_P<P>.json
    run_soc.py ... --profile [--prof-images 10 --prof-runs 1 --base-images 2]         -> results/m5_profile_<model>_P<P>.json
                                                                                      + results/m6_c4b.json (P=8 only)
    run_soc.py ... --stamps [--images 10]                                              -> results/m6_stamps_<model>_P<P>.json
    run_soc.py ... --bitexact 100 [--bitexact-baseline]                               -> results/m5_bitexact.json
Per P the emitted model.c (backend emit_dir) is built with build_fw and run on build/sim_P<P>/Vsoc_top
(soc_top -GP=<P>). Defaults overwrite; nothing is skipped. A failing run exits non-zero with the
symptom (TOHOST code, trap, bus error, timeout) and the log path.

Profile mode (SPEC section 6 C4b, DESIGN 6.5): both builds get -DMCUACCEL_PROFILE.
  * fairness: every fallback op_* of the accelerated build must have, per inference, the same cycles as
    in the baseline profile build; pass/fail and the differing ops are recorded (never hidden).
  * C4(b): meas = median over images of (c1-c0 of bench_main - 120 - sum of fallback-op intervals);
    pred = c4b_pred_cycles ('os', P=8) from `git show m1:results/m1_summary.json` (frozen).
  The window constant is read from the same frozen summary (inputs.bench_window_cycles)."""
import argparse
import datetime
import json
import os
import re
import statistics
import subprocess
import sys
import time
from array import array

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("sw", "backend", "third_party/mcugrad"):
    sys.path.insert(0, os.path.join(ROOT, p))
import build_fw  # noqa: E402

PICORV32_CFG = ("ENABLE_COUNTERS=1 ENABLE_COUNTERS64=1 ENABLE_REGS_16_31=1 ENABLE_REGS_DUALPORT=1 "
                "LATCHED_MEM_RDATA=0 TWO_STAGE_SHIFT=1 BARREL_SHIFTER=1 TWO_CYCLE_COMPARE=0 "
                "TWO_CYCLE_ALU=0 COMPRESSED_ISA=0 CATCH_MISALIGN=1 CATCH_ILLINSN=1 ENABLE_PCPI=0 "
                "ENABLE_MUL=0 ENABLE_FAST_MUL=1 ENABLE_DIV=1 ENABLE_IRQ=0 ENABLE_TRACE=0 "
                "PROGADDR_RESET=0x80000000 STACKADDR=0x80100000 (SPEC section 7)")
MODELS = ["mnist_cnn", "cifar_mobilenet"]
RES = os.path.join(ROOT, "results")
RC_TEXT = {1: "TOHOST != 1", 3: "cpu trap", 4: "cpu bus error", 5: "max_cycles reached (timeout)"}


def git(*a: str) -> str | None:
    r = subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def conv(v: str):
    for t in (int, float):
        try:
            return t(v)
        except ValueError:
            pass
    return v


def sim_binary(P: int) -> str:
    """Build (make is incremental) the Verilator SoC with the accelerator at width P into build/sim_P<P>."""
    out = os.path.join(ROOT, "build", f"sim_P{P}")
    log = os.path.join(ROOT, "build", f"make_sim_P{P}.log")
    r = subprocess.run(["make", "-C", ROOT, "sim", f"P={P}", f"OUT={out}"], capture_output=True, text=True)
    open(log, "w").write(r.stdout + r.stderr)
    if r.returncode:
        sys.exit(f"Verilator build for P={P} failed; see {log}")
    return os.path.join(out, "Vsoc_top")


def emit(model: str, P: int) -> str:
    from mcuaccel_backend.emit import emit_dir
    d = os.path.join(ROOT, "build", "emit", f"{model}_P{P}")
    emit_dir(model, P, d)
    return d


def run_sim(sim: str, hexf: str, max_cycles: int) -> dict:
    t0, c0 = time.time(), os.times()
    p = subprocess.run([sim, f"+ram={hexf}", f"+max_cycles={max_cycles}"], capture_output=True, text=True)
    wall, cpu = time.time() - t0, os.times().children_user - c0.children_user
    log = os.path.splitext(hexf)[0] + ".log"
    open(log, "w").write(p.stdout + p.stderr)
    m = re.search(r"SIM_CYCLES=(\d+)", p.stdout)
    if p.returncode != 0 or not m or "MCUGRAD_DONE" not in p.stdout:
        t = re.search(r"SIM_TOHOST=(0x[0-9a-f]+)", p.stdout)
        why = RC_TEXT.get(p.returncode, f"exit {p.returncode}") + (f", SIM_TOHOST={t.group(1)}" if t else "")
        if t and int(t.group(1), 16) == 0xACCE1:
            why += " (0xACCE1 = accelerator ERR or MCUACCEL_P/CONFIG mismatch)"
        sys.exit(f"simulation failed: {why}; sim_cycles={m.group(1) if m else '?'}; log {log}; sim {sim}")
    return {"stdout": p.stdout, "sim_cycles": int(m.group(1)), "wall_s": wall, "cpu_s": cpu, "log": log}


def result_kv(stdout: str) -> dict:
    return {k: conv(v) for k, v in re.findall(r"MCUGRAD_RESULT\s+(\w+)=(\S+)", stdout)}


def meta(b: dict, images: int, runs: int, P: int | None) -> dict:
    return {"P": P, "commit": (git("rev-parse", "HEAD") or "unknown").strip(),
            "tree_dirty": bool((git("status", "--porcelain", "--untracked-files=no") or "").strip()),
            "date": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "compiler": b["compiler"], "flags": b["flags"], "picorv32_config": PICORV32_CFG,
            "images": images, "runs": runs, "image_bytes": b["image_bytes"], "x_align": b["x_align"]}


def fw_dir(tag: str, model: str, images: int, runs: int) -> str:
    return os.path.join(ROOT, "build", "fw", f"{model}_{tag}_i{images}_r{runs}")


def put(name: str, obj: dict) -> str:
    os.makedirs(RES, exist_ok=True)
    path = os.path.join(RES, name)
    json.dump(obj, open(path, "w"), indent=2)
    return path


# ---------------------------------------------------------------- timing (C1, C1b, C5)
def timing(model: str, P: int, a) -> None:
    sim = sim_binary(P)
    b = build_fw.build(model, a.images, a.runs, model_dir=emit(model, P), out=fw_dir(f"accel_P{P}", model, a.images, a.runs))
    r = run_sim(sim, b["hex"], a.max_cycles)
    kv = result_kv(r["stdout"])
    out = {"model": model,
           "kind": f"accelerated (backend emit_dir model.c + sw/driver, P={P}); uninstrumented build; inference time per SPEC section 5",
           "mcugrad_result": kv, "sim_cycles": r["sim_cycles"], "wall_s": round(r["wall_s"], 2),
           "sim_cpu_s": round(r["cpu_s"], 2), "log": os.path.relpath(r["log"], ROOT), **meta(b, a.images, a.runs, P)}
    path = put(f"m5_accel_{model}_P{P}.json", out)
    print(f"{model} P={P}: cycles_median={kv.get('cycles_median')} (min {kv.get('cycles_min')} max {kv.get('cycles_max')}) "
          f"accuracy={kv.get('accuracy')} sim_cycles={r['sim_cycles']} wall={r['wall_s']:.1f}s -> {path}")


# ---------------------------------------------------------------- profile (fairness, C4b)
def parse_profile(stdout: str, images: int, runs: int) -> dict:
    ops = [{"op": int(m[0]), "name": m[1], "kernel": m[2], "cycles_total": int(m[3])}
           for m in re.findall(r"MCUGRAD_PROF op=(\d+) name=(\S+) kernel=(\S+) cycles=(\d+)", stdout)]
    hist = [[int(v) for v in l.split()[1:]] for l in re.findall(r"^MCUGRAD_PROFH (inf=\d+.*)$", stdout, re.M)]
    clog = re.search(r"^MCUGRAD_PROFC n=(\d+)(.*)$", stdout, re.M)
    if not ops or not hist or not clog:
        sys.exit("profile output incomplete (MCUGRAD_PROF/PROFH/PROFC missing)")
    n_inf = 1 + images * runs
    cl = [int(v) for v in clog.group(2).split()]
    # bench_main per image: c0 = bench_cycles(), micros, micros, c1 = bench_cycles() -> 4 calls (platform.c logs all)
    if len(hist) != n_inf or int(clog.group(1)) != len(cl) or len(cl) != 4 * images * runs:
        sys.exit(f"profile shape mismatch: {len(hist)} inference rows (want {n_inf}), {len(cl)} cycle-log entries "
                 f"(want {4 * images * runs}); raise PROF_MAX_INF/PROF_CLOG_MAX in sw/common/profile.h?")
    for o, col in zip(ops, zip(*hist)):
        o["per_inference"] = list(col)          # index 0 = warm-up
    intervals = [(cl[4 * j + 3] - cl[4 * j]) & 0xFFFFFFFF for j in range(images * runs)]   # timed order: run-major
    return {"ops": ops, "intervals": intervals}


def profile_build(model: str, images: int, runs: int, sim: str, max_cycles: int, model_dir: str | None, tag: str) -> dict:
    b = build_fw.build(model, images, runs, profile=True, model_dir=model_dir, out=fw_dir(tag, model, images, runs))
    r = run_sim(sim, b["hex"], max_cycles)
    return {**parse_profile(r["stdout"], images, runs), "build": b, "sim": r, "kv": result_kv(r["stdout"])}


def frozen_pred(model: str, P: int) -> tuple[int, int]:
    s = git("show", "m1:results/m1_summary.json")
    if s is None:
        sys.exit("cannot read m1:results/m1_summary.json (tag m1 missing)")
    d = json.loads(s)
    return d["models"][model]["archs"]["os"]["P"][str(P)]["c4b_pred_cycles"], d["inputs"]["bench_window_cycles"]


def profile(model: str, P: int, a, base_cache: dict) -> None:
    sim = sim_binary(P)
    if model not in base_cache:
        base_cache[model] = profile_build(model, a.base_images, a.prof_runs, sim, a.max_cycles, None, "prof_base")
    base = base_cache[model]
    acc = profile_build(model, a.prof_images, a.prof_runs, sim, a.max_cycles, emit(model, P), f"prof_accel_P{P}")
    fallback = [o for o in acc["ops"] if o["kernel"] != "mcuaccel_run"]
    chains = [o for o in acc["ops"] if o["kernel"] == "mcuaccel_run"]
    # fairness: same inference index = same image in both builds (index 0 = warm-up image 0)
    bops = {(o["op"], o["name"]): o for o in base["ops"]}
    n_cmp = min(len(base["ops"][0]["per_inference"]), len(acc["ops"][0]["per_inference"]))
    diffs = []
    for o in fallback:
        bo = bops.get((o["op"], o["name"]))
        if bo is None:
            diffs.append({"op": o["op"], "name": o["name"], "reason": "not in baseline profile"})
            continue
        bad = [j for j in range(n_cmp) if o["per_inference"][j] != bo["per_inference"][j]]
        if bad:
            diffs.append({"op": o["op"], "name": o["name"], "reason": "per-inference cycles differ",
                          "inferences_compared": n_cmp, "differing_inferences": bad,
                          "baseline": [bo["per_inference"][j] for j in bad], "accelerated": [o["per_inference"][j] for j in bad],
                          "max_abs_delta": max(abs(o["per_inference"][j] - bo["per_inference"][j]) for j in bad),
                          "max_rel_delta_pct": round(max(100.0 * abs(o["per_inference"][j] - bo["per_inference"][j]) / bo["per_inference"][j] for j in bad), 4)})
    fairness = {"pass": not diffs, "fallback_ops": [(o["op"], o["name"]) for o in fallback],
                "inferences_compared": n_cmp, "differing_ops": diffs}
    # C4(b): per timed inference (run-major), then median over runs per image, then median over images
    pred, window = frozen_pred(model, P)
    fb_idx = [k for k, o in enumerate(acc["ops"]) if o["kernel"] != "mcuaccel_run"]
    images, runs = a.prof_images, a.prof_runs
    meas_t = []
    for t, iv in enumerate(acc["intervals"]):
        col = 1 + t                     # hist index 0 is the warm-up
        fb = sum(acc["ops"][k]["per_inference"][col] for k in fb_idx)
        meas_t.append(iv - window - fb)
    per_image = [statistics.median(meas_t[r * images + i] for r in range(runs)) for i in range(images)]
    meas = int(round(statistics.median(per_image)))
    err = 100.0 * (pred - meas) / meas
    c4b = {"pred": pred, "meas": meas, "signed_error_pct": round(err, 3), "within_10pct": abs(err) <= 10.0}
    n_acc = 1 + images * runs
    for o in acc["ops"]:
        o["cycles_per_inference"] = o["cycles_total"] / n_acc
    out = {"model": model, "P": P,
           "kind": "PROFILE builds (-DMCUACCEL_PROFILE): rdcycle around each op_*/chain_* call plus per-inference history; "
                   "instrumentation cost is inside the measured intervals (SPEC/T1.2 note). Headline cycles: m5_accel_*.json.",
           "fairness": fairness, "c4b": {**c4b, "bench_window_cycles": window,
                                         "pred_source": "git show m1:results/m1_summary.json models.<m>.archs.os.P.<P>.c4b_pred_cycles",
                                         "per_image_meas": per_image, "interval_c1_minus_c0_per_timed_inference": acc["intervals"],
                                         "fallback_sum_per_image_run0": [sum(acc["ops"][k]["per_inference"][1 + i] for k in fb_idx) for i in range(images)]},
           "accelerated_ops": [{k: v for k, v in o.items() if k != "per_inference"} for o in acc["ops"]],
           "accelerated_chains": [(o["op"], o["name"]) for o in chains],
           "baseline_ops": [{k: v for k, v in o.items() if k != "per_inference"} for o in base["ops"]],
           "baseline_profile_images": a.base_images, "accelerated_profile_images": images, "profile_runs": runs,
           "sim_cycles_accel": acc["sim"]["sim_cycles"], "sim_cycles_base": base["sim"]["sim_cycles"],
           "logs": {"accel": os.path.relpath(acc["sim"]["log"], ROOT), "baseline": os.path.relpath(base["sim"]["log"], ROOT)},
           **meta(acc["build"], images, runs, P)}
    path = put(f"m5_profile_{model}_P{P}.json", out)
    print(f"{model} P={P}: fairness={'PASS' if fairness['pass'] else 'FAIL ' + str([d['name'] for d in diffs])} "
          f"C4b pred={pred} meas={meas} err={err:+.2f}% -> {path}")
    if P == 8:      # C4(b) is scored at P=8 only
        p = os.path.join(RES, "m6_c4b.json")
        cur = json.load(open(p)) if os.path.exists(p) else {}
        cur[model] = {"pred": pred, "meas": meas}
        put("m6_c4b.json", cur)


# ---------------------------------------------------------------- measured STAMPs (C4a, C5)
def command_list(model: str, P: int) -> tuple[list, list]:
    """-> ([[op, split index]] in execution order, commands per chain call); op = graph node index, as in m1_predictions.csv."""
    from mcuaccel_backend.emit import plan
    from mcuaccel_backend.graphs import load_int8_graph
    from mcuaccel_backend.layers import from_node
    from mcuaccel_backend.split import build_commands
    g = load_int8_graph(model)
    _, chains, _ = plan(g)
    cmds, per_chain = [], []
    for members in chains:
        n0 = len(cmds)
        for i in members:
            cmds += [[i, k] for k in range(len(build_commands(from_node(g, g.nodes[i]), P, 0, 0, 0, 0, 0, 0x1000)))]
        per_chain.append(len(cmds) - n0)
    return cmds, per_chain


def stamps(model: str, P: int, a) -> None:
    """Instrumented build (-DMCUACCEL_STAMPS: the driver logs CYCLES and every descriptor's STAMP after each chain);
    warm-up + `images` inferences (1 run). Not a timed build; headline cycles come from timing()."""
    sim = sim_binary(P)
    b = build_fw.build(model, a.images, 1, model_dir=emit(model, P), out=fw_dir(f"accel_P{P}_stamps", model, a.images, 1),
                       defines=["MCUACCEL_STAMPS"])
    r = run_sim(sim, b["hex"], a.max_cycles)
    chains = [(int(c), int(cy), int(n), [int(v) for v in rest.split()])
              for c, cy, n, rest in re.findall(r"^MCUACCEL_STAMPS chain=(\d+) cycles=(\d+) ncmd=(\d+) :((?: \d+)*)$", r["stdout"], re.M)]
    cmds, per_chain = command_list(model, P)
    nc = len(per_chain)
    if "MCUACCEL_STAMPS_DONE" not in r["stdout"] or len(chains) != nc * (1 + a.images) or any(
            len(st) != n or n != per_chain[j % nc] for j, (_, _, n, st) in enumerate(chains)):
        sys.exit(f"STAMP log shape mismatch ({len(chains)} chain calls, want {nc} x {1 + a.images}); log {r['log']}")
    out = {"model": model, "P": P, "kind": "STAMP/CYCLES log of an instrumented build (-DMCUACCEL_STAMPS), warm-up + images, 1 run; inference 0 = warm-up",
           "commands": cmds, "commands_per_chain": per_chain,
           "inferences": [[{"cycles": cy, "stamps": st} for (_, cy, _, st) in chains[k * nc:(k + 1) * nc]] for k in range(1 + a.images)],
           "log": os.path.relpath(r["log"], ROOT), **meta(b, a.images, 1, P)}
    path = put(f"m6_stamps_{model}_P{P}.json", out)
    print(f"{model} P={P}: {len(cmds)} commands in {nc} chains, {1 + a.images} inferences -> {path}")


# ---------------------------------------------------------------- bit-exactness (C2c)
def ref_images(model: str, n: int) -> tuple[list, list]:
    """First n quantised test images from the committed testset.c head (the firmware's own images are
    cross-checked against it by sw/gen_testset.py) and the int8 graph reference outputs."""
    from mcugrad.quant import run_int8
    from mcuaccel_backend.graphs import load_int8_graph
    g = load_int8_graph(model)
    shape = g.values[g.inputs[0]].shape
    nb = 1
    for d in shape:
        nb *= d
    head = open(os.path.join(build_fw.MG, "generated", model, "testset.c")).read(n * nb * 8 + 8192).split("= {", 1)[1]
    v = [int(t) for t in re.findall(r"-?\d+", head)][: n * nb]
    assert len(v) == n * nb, "committed testset.c has fewer images than requested"
    xs = [array("b", v[i * nb:(i + 1) * nb]) for i in range(n)]
    return xs, [list(run_int8(g, {g.inputs[0]: (x, shape)})[g.outputs[0]]) for x in xs]


def dump_outputs(model: str, n: int, sim: str, model_dir: str | None, tag: str, max_cycles: int) -> tuple[list, dict]:
    b = build_fw.build(model, n, 1, model_dir=model_dir, out=fw_dir(tag, model, n, 1), defines=["BENCH_DUMP_ARGMAX"])
    r = run_sim(sim, b["hex"], max_cycles)
    outs = {int(m[0]): [int(x) for x in m[3].split()]
            for m in re.findall(r"^MCUGRAD_ARGMAX (\d+) (\d+) (\d+)((?: -?\d+)*)$", r["stdout"], re.M)}
    return [outs.get(i) for i in range(n)], r


def mismatch(got: list, want: list) -> tuple[int, list]:
    """(mismatching bytes, mismatching image indices); a missing/short output counts every byte."""
    tot, bad = 0, []
    for i, (g, w) in enumerate(zip(got, want)):
        m = len(w) if g is None or len(g) != len(w) else sum(x != y for x, y in zip(g, w))
        if m:
            tot, bad = tot + m, bad + [i]
    return tot, bad


def bitexact(model: str, P: int, a) -> None:
    n = a.bitexact
    sim = sim_binary(P)
    _, ref = ref_images(model, n)
    got, r = dump_outputs(model, n, sim, emit(model, P), f"accel_P{P}_dump", a.max_cycles)
    mm, bad = mismatch(got, ref)
    ent = {"P": P, "images": n, "mismatch_bytes": mm, "mismatch_images": bad, "reference": "mcugrad.quant.run_int8",
           "sim_cycles": r["sim_cycles"], "log": os.path.relpath(r["log"], ROOT)}
    if a.bitexact_baseline:
        bgot, br = dump_outputs(model, n, sim, None, "base_dump", a.max_cycles)
        bm, bbad = mismatch(got, bgot)
        ent["vs_baseline_firmware"] = {"mismatch_bytes": bm, "mismatch_images": bbad, "log": os.path.relpath(br["log"], ROOT)}
        ent["baseline_firmware_vs_run_int8_mismatch_bytes"] = mismatch(bgot, ref)[0]
    p = os.path.join(RES, "m5_bitexact.json")
    cur = json.load(open(p)) if os.path.exists(p) else {}
    cur.setdefault("per_model_c2c", {})[model] = ent
    pm = cur["per_model_c2c"]
    if all(m in pm for m in MODELS):     # flat fields only once both networks are covered
        cur["c2c_images"] = min(e["images"] for e in pm.values())
        cur["c2c_mismatch"] = sum(e["mismatch_bytes"] for e in pm.values())
    cur["c2b_note"] = cur.get("c2b_note", "C2b (layer-by-layer, accelerator level) is filled by tb/accel, not by run_soc.py")
    cur.update({"commit": (git("rev-parse", "HEAD") or "unknown").strip(),
                "date": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")})
    path = put("m5_bitexact.json", cur)
    print(f"{model} P={P} C2c: {n} images, mismatching bytes={mm} (images {bad}) -> {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", action="append", choices=MODELS)
    ap.add_argument("--P", type=int, action="append", choices=[1, 2, 4, 8, 16])
    ap.add_argument("--images", type=int, default=10)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--prof-images", type=int, default=10, help="accelerated profile build (C4b needs 10)")
    ap.add_argument("--base-images", type=int, default=2, help="baseline profile build (fairness only; slow)")
    ap.add_argument("--prof-runs", type=int, default=1)
    ap.add_argument("--stamps", action="store_true", help="STAMP/CYCLES log for C4a/C5 (see experiments/compare_m1.py)")
    ap.add_argument("--bitexact", type=int, metavar="N", help="C2c over N images (SPEC: 100)")
    ap.add_argument("--bitexact-baseline", action="store_true", help="also run the baseline firmware on the N images (slow) and compare")
    ap.add_argument("--max-cycles", type=int, default=20_000_000_000)
    a = ap.parse_args()
    cache: dict = {}
    for m in a.model or MODELS:
        for P in a.P or [8]:
            if a.stamps:
                stamps(m, P, a)
            elif a.profile:
                profile(m, P, a, cache)
            elif a.bitexact:
                bitexact(m, P, a)
            else:
                timing(m, P, a)
