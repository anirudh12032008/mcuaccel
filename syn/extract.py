#!/usr/bin/env python3
"""nextpnr report/log + Yosys stat -> one JSONL row with provenance (append)."""
import argparse
import datetime
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CELLS = ["LUT4", "CCU2C", "PFUMX", "L6MUX21", "TRELLIS_FF", "DP16KD", "MULT18X18D"]


def run(*cmd):
    env = dict(os.environ, HOME=str(ROOT / ".cache/home"))
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env)
        return (r.stdout + r.stderr).strip()
    except OSError:
        return ""


def first(s):
    return s.splitlines()[0] if s else "unknown"


def yosys_cells(stat_path):
    """Cell counts by type from `stat` ('  <count>  <type>' lines, optional area column)."""
    out = {c: 0 for c in CELLS}
    for line in open(stat_path):
        t = line.split()
        if len(t) >= 2 and t[0].isdigit() and t[-1] in out:
            out[t[-1]] = int(t[0])
        elif len(t) >= 2 and t[0] in out and t[-1].isdigit():
            out[t[0]] = int(t[-1])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("log")
    ap.add_argument("stat")
    ap.add_argument("out")
    ap.add_argument("--design", required=True)
    ap.add_argument("--p", default="null")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--freq", type=float, required=True)
    ap.add_argument("--wall", type=int, required=True)
    ap.add_argument("--json", required=True)  # Yosys netlist (wrapper port-bit count)
    ap.add_argument("--top", required=True)
    a = ap.parse_args()

    rep = json.load(open(a.report))
    util = {k: v["used"] for k, v in rep["utilization"].items()}
    fm = re.findall(r"Max frequency for clock\s+'?[^':]+'?:\s*([\d.]+) MHz", open(a.log).read())
    fmax = float(fm[-1]) if fm else next(iter(rep["fmax"].values()))["achieved"]
    mod = json.load(open(a.json))["modules"][a.top]
    port_bits = sum(len(p["bits"]) for n, p in mod["ports"].items() if n != "clk")
    stub = "WALKING-SKELETON STUB" in (ROOT / "rtl/accel/mcuaccel_top.sv").read_text()
    row = {
        "design": a.design,
        "P": None if a.p == "null" else int(a.p),
        "seed": a.seed,
        "freq_target": a.freq,
        "fmax_mhz": round(fmax, 2),
        "timing_met": fmax >= a.freq,
        "lut4": util.get("TRELLIS_COMB", 0),  # TRELLIS_COMB used (SPEC 5)
        "ff": util.get("TRELLIS_FF", 0),  # raw; wrapper FFs = ff_port_bits at most (SPEC 5)
        "ff_port_bits": port_bits if a.design in ("accel", "picorv32") else 0,
        "dp16kd": util.get("DP16KD", 0),
        "mult18x18d": util.get("MULT18X18D", 0),
        "lutram": util.get("TRELLIS_RAMW", 0),
        "yosys_cells": yosys_cells(a.stat),
        "utilization": util,
        "device": "LFE5U-85F",
        "package": "CABGA381",
        "speed": 6,
        "commit": first(run("git", "rev-parse", "HEAD")),
        "dirty": run("git", "status", "--porcelain", "--untracked-files=no") != "",
        "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tools": {
            "yosys": first(run("yosys", "-V")),
            "nextpnr_ecp5": first(run("nextpnr-ecp5", "--version")),
        },
        "wall_s": a.wall,
    }
    if stub and a.design in ("accel", "soc"):
        row["note"] = "walking-skeleton stub"
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "a") as f:
        f.write(json.dumps(row) + "\n")
    print(f"{a.out}: {a.design} P={row['P']} seed={a.seed} lut4={row['lut4']} ff={row['ff']} "
          f"dp16kd={row['dp16kd']} mult={row['mult18x18d']} fmax={row['fmax_mhz']} MHz wall={a.wall}s")


if __name__ == "__main__":
    main()
