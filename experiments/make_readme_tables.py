#!/usr/bin/env python3
"""results/ -> tables between <!-- BEGIN:x --> / <!-- END:x --> markers in README.md. Pending = no result file yet."""
import re
from status import *  # noqa: F401,F403

README = ROOT / "README.md"


def table(head, rows):
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] +
                     ["| " + " | ".join(map(str, r)) + " |" for r in rows])


def results():
    t = [table(["claim", "statement", "status", "evidence"], [(i, s, f"**{v}**", e) for i, s, v, e in claims()])]
    rows = []
    for m in MODELS:
        b, a, ps = baseline(m), accel(m, 8), predicted_speedup(m)
        rows.append((m, n(b), n(a), n(b / a if b and a else None, 1), n(ps, 1)))
    t.append(table(["network", "baseline cycles (median)", "accelerated cycles, P=8", "speedup", "pre-registered prediction"], rows))
    rows = []
    for m in MODELS:
        rows.append([m] + [n(baseline(m) / accel(m, P) if baseline(m) and accel(m, P) else None, 1) for P in PS])
    t.append("Speedup vs P (end to end, fallback layers and driver included):\n\n" + table(["network"] + [f"P={P}" for P in PS], rows))
    rows = []
    for d, P, lab in [("picorv32", None, "PicoRV32 (SPEC config)"), ("soc_noaccel", None, "SoC without accelerator")] + \
                     [("accel", P, f"accelerator P={P}") for P in PS] + [("soc", 8, "SoC with accelerator P=8")]:
        rs, r = syn_row(d, P)
        f = n(statistics.median(x["fmax_mhz"] for x in rs) if rs else None, 1)
        rows.append((lab, n(r and r["lut4"]), n(r and r["ff"]), n(r and r["mult18x18d"]), n(r and r["dp16kd"]), f, len(rs) or "pending"))
    t.append("ECP5 LFE5U-85F, Yosys + nextpnr, 50 MHz target (LUT4/FF/MULT/BRAM from seed 1, Fmax median over seeds):\n\n" +
             table(["design", "LUT4", "FF", "MULT18X18D", "DP16KD", "Fmax MHz", "seeds"], rows))
    return "\n\n".join(t)


def prereg_block():
    c, sha = prereg()
    return (f"- tag `m1` commit: `{c or 'unknown (tag not found)'}`\n"
            f"- sha256 of `results/m1_predictions.csv` at that tag (`git show m1:results/m1_predictions.csv | shasum -a 256`): "
            f"`{sha or 'unknown'}`\n")


def runtimes():
    rows = []
    for m in MODELS:
        rows.append((f"baseline {m} (10 images x 3 runs)", f"results/baseline_{m}.json", wall([R / f"baseline_{m}.json"])))
        rows.append((f"baseline profile {m}", f"results/baseline_profile_{m}.json", wall([R / f"baseline_profile_{m}.json"])))
    for m in MODELS:
        rows.append((f"accelerated {m}, P=8 timing", f"results/m5_accel_{m}_P8.json", wall([R / f"m5_accel_{m}_P8.json"])))
    m3 = load(R / "m3_summary.json")
    rows.append(("M3 campaign (1000 layers x 5 P, chains, errors, 10-image networks)", "results/m3_summary.json wall_secs",
                 m3 and m3.get("wall_secs")))
    sy = sum(r.get("wall_s", 0) for p in (R / "syn").glob("*.jsonl") for r in jsonl(p))
    rows.append(("synthesis sweep, sum of job wall_s (run 3 at a time: divide by about 3)", "results/syn/*.jsonl wall_s", sy or None))
    return table(["step", "provenance", "wall seconds"], [(a, b, n(c, 0)) for a, b, c in rows])


def fill(text, tag, body):
    pat = re.compile(rf"(<!-- BEGIN:{tag} -->).*?(<!-- END:{tag} -->)", re.S)
    assert pat.search(text), f"marker {tag} missing in README.md"
    return pat.sub(lambda m: m.group(1) + "\n" + body.strip("\n") + "\n" + m.group(2), text)


if __name__ == "__main__":
    t = README.read_text()
    for tag, fn in [("results", results), ("prereg", prereg_block), ("runtimes", runtimes)]:
        t = fill(t, tag, fn())
    README.write_text(t)
