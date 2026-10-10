#!/usr/bin/env python3
"""T1.0: measure the driver's fixed cost on the walking-skeleton STUB accelerator.
    run_drv_timing.py [--max-cycles N]
Builds sw/apps/drv_timing.c + sw/driver/mcuaccel.c with build_fw's flags, runs the Verilator SoC,
parses MCUGRAD_RESULT lines, disassembles mcuaccel_run and writes results/driver_overhead.json.
The stub returns DONE one cycle after START and fetches nothing, so drv_chain = pure driver cost."""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "sw"))
sys.path.insert(0, os.path.join(ROOT, "experiments"))
sys.path.insert(0, os.path.join(ROOT, "model"))
import build_fw  # noqa: E402
import m1  # noqa: E402
import run_baseline as rb  # noqa: E402


def disasm_run(elf: str) -> dict:
    """Instruction sequence of mcuaccel_run and of its poll loop (the backward-branch loop)."""
    txt = subprocess.run(["riscv-none-elf-objdump", "-d", "-M", "no-aliases", elf], capture_output=True,
                         text=True, check=True).stdout
    m = re.search(r"<mcuaccel_run>:\n(.*?)\n\n", txt, re.S)
    ins = []
    for ln in m.group(1).splitlines():
        p = ln.split("\t")
        if len(p) >= 3:
            ins.append((int(p[0].strip().rstrip(":"), 16), re.sub(r"\s+#.*", "", p[2]).strip() + " " +
                        re.sub(r"\s+#.*", "", p[3]).strip() if len(p) > 3 else p[2].strip()))
    loop = None
    for i, (pc, t) in enumerate(ins):          # backward branch = the poll loop
        mm = re.match(r"(bne|beq|bge|blt|bgeu|bltu)\s.*?,([0-9a-f]+) <", t.replace("\t", " "))
        if mm and int(mm.group(2), 16) < pc:
            j = next(k for k, (q, _) in enumerate(ins) if q == int(mm.group(2), 16))
            loop = [x[1] for x in ins[j:i + 1]]
    return {"mcuaccel_run": [f"{pc:x}: {t}" for pc, t in ins], "poll_loop": loop}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-cycles", type=int, default=50_000_000)
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "driver_overhead.json"))
    a = ap.parse_args()
    b = build_fw.build_app([os.path.join(ROOT, "sw", "apps", "drv_timing.c"),
                            os.path.join(ROOT, "sw", "driver", "mcuaccel.c")],
                           os.path.join(ROOT, "build", "fw", "drv_timing"))
    r = rb.run_sim(b["hex"], a.max_cycles)
    kv = {k: rb.conv(v) for k, v in re.findall(r"MCUGRAD_RESULT\s+(\w+)=(\S+)", r["stdout"])}
    for k in ("drv_rdcycle", "drv_chain", "drv_patch"):
        if k not in kv:
            sys.exit(f"missing {k}; log {r['log']}")
    med = {n: kv[f"drv_{n}_med"] for n in ("rdcycle", "chain", "p1", "p2", "p5")}
    xs, ys = [1, 2, 5], [med["p1"], med["p2"], med["p5"]]
    mx, my = sum(xs) / 3, sum(ys) / 3
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    dis = disasm_run(b["elf"])
    out = {
        "kind": "driver fixed cost measured on the walking-skeleton STUB accelerator (T1.0)",
        "note": "The stub returns DONE one cycle after START and fetches no descriptor, so drv_chain "
                "(= median chain window minus the empty rdcycle-pair window) is the pure driver cost "
                "of one mcuaccel_run call including its call-site; with the real accelerator the "
                "CPU polls while it runs and only the exit phase of the poll loop is added. "
                "drv_patch = (p5 - p1)/4 cycles per patched descriptor field; patch_slope_lsq is the "
                "least-squares slope over 1, 2 and 5 fields. Codegen is the compiler's: the call "
                "site hoisted &ad into a register (mv, 1 ALU) where a straight-line model_run needs "
                "lui+addi (2 ALU), and fields beyond the second need a lui+addi address each.",
        "drv_chain": kv["drv_chain"], "drv_patch": kv["drv_patch"], "drv_rdcycle": kv["drv_rdcycle"],
        "medians": med, "min": {n: kv[f"drv_{n}_min"] for n in ("rdcycle", "chain", "p1", "p2", "p5")},
        "max": {n: kv[f"drv_{n}_max"] for n in ("rdcycle", "chain", "p1", "p2", "p5")},
        "cpi_estimate": {"drv_chain": m1.T_DRV_CHAIN, "drv_patch": m1.T_DRV_PATCH,
                         "source": "model/m1.py CPI table (DESIGN 9.5), the pre-measurement estimate"},
        "patch_slope_lsq": round(slope, 3), "reps": 100,
        "disassembly": dis["mcuaccel_run"], "poll_loop": dis["poll_loop"],
        "poll_loop_string": "; ".join(re.sub(r"\s+", " ", x) for x in (dis["poll_loop"] or [])),
        "sim_cycles": r["sim_cycles"], "commit": rb.git_head(),
        "date": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "compiler": b["compiler"], "flags": b["flags"], "picorv32_config": rb.PICORV32_CFG,
        "accelerator": "walking-skeleton stub rtl/accel/mcuaccel_top.sv (BUSY one cycle after START)",
    }
    json.dump(out, open(a.out, "w"), indent=2)
    print({k: out[k] for k in ("drv_rdcycle", "drv_chain", "drv_patch", "patch_slope_lsq", "medians", "poll_loop_string")})


if __name__ == "__main__":
    main()
