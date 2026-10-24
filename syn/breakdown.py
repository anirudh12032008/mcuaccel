#!/usr/bin/env python3
"""Per-module Yosys cells (synth_ecp5 -noflatten) + flattened totals + post-place row -> one JSON.

Reads build/syn/breakdown_P<P>/{hier,flat}.json (written by syn/breakdown.sh) and the last row of
results/syn/accel_P<P>.jsonl (post-place, nextpnr). lut_equiv = LUT4 + 2*CCU2C + 4*TRELLIS_DPR16X4,
i.e. nextpnr's pre-pack "logic + carry + RAM LUTs"; the RAMW LUTs of a DPR16X4 land in TRELLIS_RAMW,
not TRELLIS_COMB. Post-place TRELLIS_COMB minus flat lut_equiv = LUTs the packer adds (route-through).
"""
import datetime
import json
import re
import sys

from extract import ROOT, first, run  # same provenance helpers as the per-run rows

CELLS = ["LUT4", "CCU2C", "PFUMX", "L6MUX21", "TRELLIS_FF", "MULT18X18D", "DP16KD", "TRELLIS_DPR16X4"]


def counts(by_type):
    c = {k: by_type.get(k, 0) for k in CELLS}
    c["lut_equiv"] = c["LUT4"] + 2 * c["CCU2C"] + 4 * c["TRELLIS_DPR16X4"]
    return c


def short(name):
    """'$paramod\\mcuaccel_ctrl\\P=s32...' or '\\mcuaccel_fetch' -> 'ctrl' / 'fetch'; top -> glue."""
    m = re.search(r"mcuaccel_(\w+?)(?:_wrap)?(?:\\|$)", name)
    n = m.group(1) if m else name
    if "_wrap" in name:
        return "wrapper"
    return "top_glue" if n == "top" else n


def total(mods):
    t = {k: sum(m[k] for m in mods.values()) for k in CELLS}
    t["lut_equiv"] = sum(m["lut_equiv"] for m in mods.values())
    return t


def main():
    out, ps = sys.argv[1], [int(p) for p in sys.argv[2:]]
    recs = []
    for p in ps:
        b = ROOT / f"build/syn/breakdown_P{p}"
        hier = json.load(open(b / "hier.json"))
        flat = json.load(open(b / "flat.json"))
        mods = {short(n): counts(m["num_cells_by_type"]) for n, m in hier["modules"].items()}
        mods = dict(sorted(mods.items(), key=lambda kv: -kv[1]["lut_equiv"]))
        ht = total(mods)
        (fname, fm), = flat["modules"].items()
        ft = counts(fm["num_cells_by_type"])
        rows = (ROOT / f"results/syn/accel_P{p}.jsonl").read_text().splitlines()
        pp = json.loads(rows[-1])
        assert pp["yosys_cells"]["LUT4"] == ft["LUT4"], "flat re-synthesis differs from the jsonl row"
        place = {k: pp[k] for k in ("lut4", "ff", "mult18x18d", "dp16kd", "lutram", "seed", "commit")}
        for m in mods.values():
            m["share_lut_equiv"] = round(m["lut_equiv"] / ht["lut_equiv"], 4)
        recs.append({
            "P": p,
            "modules": mods,
            "hier_total": ht,
            "flat_total": ft,
            "postplace": place,
            "gap": {
                "hier_minus_flat_lut_equiv": ht["lut_equiv"] - ft["lut_equiv"],
                "hier_minus_flat_ff": ht["TRELLIS_FF"] - ft["TRELLIS_FF"],
                "postplace_minus_flat_lut_equiv": pp["lut4"] - ft["lut_equiv"],
            },
        })
    doc = {
        "design": "accel",
        "top": "mcuaccel_top_wrap",
        "flow": "yosys synth_ecp5 -noflatten (modules) and synth_ecp5 (flat); postplace from accel_P<P>.jsonl last row",
        "lut_equiv": "LUT4 + 2*CCU2C + 4*TRELLIS_DPR16X4 (nextpnr pre-pack logic+carry+RAM LUTs)",
        "records": recs,
        "commit": first(run("git", "rev-parse", "HEAD")),
        "dirty": run("git", "status", "--porcelain", "--untracked-files=no") != "",
        "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tools": {"yosys": first(run("yosys", "-V"))},
    }
    with open(out, "w") as f:
        json.dump(doc, f, indent=1)
        f.write("\n")
    for r in recs:
        print(f"P={r['P']}: " + " ".join(f"{k}={m['lut_equiv']}" for k, m in r["modules"].items())
              + f" | hier={r['hier_total']['lut_equiv']} flat={r['flat_total']['lut_equiv']}"
              f" place={r['postplace']['lut4']}")


if __name__ == "__main__":
    main()
