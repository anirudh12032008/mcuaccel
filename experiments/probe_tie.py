#!/usr/bin/env python3
"""Post-hoc PSETUP-tie probe for the C4a rows over their registered bias (DESIGN 7.5, DECISIONS D21).

Runs one conv command of the layer's shape on mcuaccel_top (Verilator, cocotb, tb/accel harness), traces the
ctrl state and the master port every cycle and splits pred - meas of the frozen m1 prediction into measured terms:

  setup   (DESC..PLOAD_WAIT)   model t_desc + t_wload + t_pload   vs first DESC request .. first PSETUP
  compute (PSETUP + ISSUE)     model t_compute                    vs first PSETUP .. first FLUSH
  tail    (FLUSH .. STAMP)     model t_flush                      vs first FLUSH .. STAMP request
  compute = n_pix (+1 PSETUP) + reads + writes + requant/credit slack (model)
          = PSETUP cycles + ISSUE reads + ISSUE writes + ISSUE idle  (RTL, one access per cycle)
A write granted in a PSETUP cycle takes no ISSUE slot: one cycle the model charges and the RTL does not.
`tie` = such writes at pixels whose pending write belongs to a tile with lam*(R+W) == max(NV, credit bound).

Usage: python experiments/probe_tie.py   (writes results/m6_tie_probe.json)
Rebuilds nothing in rtl/; the model is imported read-only and its t_cmd is asserted equal to the frozen prediction.
Weights/data are random (cycles do not depend on values; the bus STAMP is asserted equal to the measured STAMP).
"""
import csv, json, os, platform, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
S_PSETUP, S_ISSUE, S_FLUSH = 7, 8, 9   # rtl/accel/mcuaccel_ctrl.sv
MARGIN_PP = 0.5                         # experiments/make_deviations.py MARGIN
# (model, op, P) of results/deviations.json root_cause_A.exceeding_rows
ROWS = [("mnist_cnn", 1, 8), ("mnist_cnn", 1, 16), ("mnist_cnn", 3, 16),
        ("cifar_mobilenet", 0, 4), ("cifar_mobilenet", 0, 8), ("cifar_mobilenet", 0, 16)]


# ---------------------------------------------------------------- cocotb side (runs inside the simulator)
try:
    import cocotb
    from cocotb.triggers import ReadOnly, RisingEdge
except ImportError:          # host side without cocotb on the path is fine
    cocotb = None

if cocotb is not None and os.environ.get("TIE_SHAPE"):
    @cocotb.test()
    async def probe(dut):
        sys.path[:0] = [str(ROOT / "tb"), str(ROOT / "backend"), str(ROOT / "backend/tests")]
        import numpy as np
        from accel import harness as H
        from accel.gen_layers import Item, build_image, make_layer
        b = await H.Bench.create(dut)
        r = np.random.default_rng(1)
        h, w, c, o, k, s, p = (int(v) for v in os.environ["TIE_SHAPE"].split(","))
        L = make_layer(r, "conv", h, w, c, o, k, k, s, s, p, p)
        x = bytes(r.integers(0, 256, h * w * c, dtype=np.uint8))
        img = build_image([Item(L, x, 0, 0)], b.P, 1)
        tr = []

        async def mon():
            while True:
                await RisingEdge(dut.clk)
                await ReadOnly()
                req, we, gnt = int(dut.m_req.value), int(dut.m_we.value), int(dut.m_gnt.value)
                tr.append((int(dut.u_ctrl.state.value), req & gnt & we, req & gnt & (1 - we), req, we,
                           int(dut.m_addr.value)))
        cocotb.start_soon(mon())
        res = await b.run_image(img)
        Path(os.environ["TIE_OUT"]).write_text(json.dumps(dict(trace=tr, stamp_addr=img.stamp_addrs[0],
                                                               stamps=res.stamps, mismatches=res.mismatches)))


# ---------------------------------------------------------------- host side
def git(*a):
    return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True).stdout.strip()


def run_rtl(L, P, out):
    from cocotb_tools.runner import get_runner
    build = ROOT / "build" / "tie_probe" / f"sim_P{P}"
    rn = get_runner("verilator")
    rn.build(sources=[str(s) for s in sorted((ROOT / "rtl/accel").glob("*.sv"))], hdl_toplevel="mcuaccel_top",
             build_dir=build, parameters={"P": P}, always=False,
             build_args=["-Wno-fatal", "--timing", "--public-flat-rw", str(ROOT / "syn/lint.vlt")])
    shape = ",".join(str(L[k]) for k in ("H", "W", "C", "O", "KH", "SH", "PH"))
    rn.test(hdl_toplevel="mcuaccel_top", test_module="probe_tie", test_dir=str(HERE), build_dir=build,
            results_xml=str(build / "probe_tie.xml"),
            extra_env={"TIE_SHAPE": shape, "TIE_OUT": str(out), "MCUACCEL_OUT": str(build / "out"),
                       "PYTHONPATH": f"{ROOT / 'tb'}:{ROOT / 'backend'}:{ROOT / 'backend/tests'}:"
                                     f"{os.environ.get('PYTHONPATH', '')}"})
    return json.loads(Path(out).read_text())


def model_terms(L, P):
    """Frozen m1 model, decomposed: per-pixel class of the last tile (whose write is pending at PSETUP)."""
    import numpy as np
    sys.path.insert(0, str(ROOT / "model"))
    import m1
    cfg = m1.Cfg()
    (o_s, nch), = m1.commands(L, P, cfg)
    row = m1._command_row("x", L, "os", P, cfg, 0, o_s, nch)
    tiles = []
    os_ = m1._cmd_os(L, P, cfg, o_s, nch, tiles_out=tiles)
    third = lambda nv: max(nv, m1.cdiv(nv + cfg.lam + cfg.l_mac + 1, cfg.credits))
    port = [cfg.lam * (t["R"] + t["W"]) for t in tiles]
    slack = sum(int((t["T"] - pt).sum()) for t, pt in zip(tiles, port))
    last_port, last_q = port[-1].ravel(), third(tiles[-1]["NV"])
    cls = np.where(last_port == last_q, "tie", np.where(last_port > last_q, "port", "requant"))
    n_pix = last_port.size
    tie_tiles = sum(int((pt == third(t["NV"])).sum()) for t, pt in zip(tiles, port))
    return dict(row=row, n_pix=n_pix, reads=os_["x_words"], writes=os_["wr"], slack=slack, cls=cls.tolist(), tie_tiles=tie_tiles,
                n_tiles=len(tiles), NV=[t["NV"] for t in tiles],
                pixel_class_counts={c: int((cls == c).sum()) for c in ("tie", "port", "requant")})


def analyse(tr, stamp_addr, m):
    first = next(i for i, t in enumerate(tr) if t[3])
    ps0 = next(i for i, t in enumerate(tr) if t[0] == S_PSETUP)
    fl0 = next(i for i, t in enumerate(tr) if t[0] == S_FLUSH)
    st = next(i for i, t in enumerate(tr) if t[3] and t[4] and t[5] == stamp_addr)
    comp = tr[ps0:fl0]
    other = sum(1 for t in comp if t[0] not in (S_PSETUP, S_ISSUE))
    assert other == 0, f"{other} compute-window cycles outside PSETUP/ISSUE"
    entries = [i for i in range(ps0, fl0) if tr[i][0] == S_PSETUP and tr[i - 1][0] != S_PSETUP]
    assert len(entries) == m["n_pix"], (len(entries), m["n_pix"])
    wr_ps_cls = {"tie": 0, "port": 0, "requant": 0}
    for k, e in enumerate(entries[1:], 1):
        j = e
        while j < fl0 and tr[j][0] == S_PSETUP:
            if tr[j][1]:
                wr_ps_cls[m["cls"][k - 1]] += 1   # pending write of the previous pixel
            j += 1
    iss = [t for t in comp if t[0] == S_ISSUE]
    meas = dict(bus_stamp=st - first, setup=ps0 - first, compute=fl0 - ps0, tail=st - fl0,
                psetup_cycles=sum(1 for t in comp if t[0] == S_PSETUP),
                issue_reads=sum(t[2] for t in iss), issue_writes=sum(t[1] for t in iss),
                issue_idle=sum(1 for t in iss if not t[1] and not t[2]),
                psetup_writes=sum(t[1] for t in comp if t[0] == S_PSETUP),
                writes_after_compute=sum(t[1] for t in tr[fl0:st]),
                writes_total=sum(t[1] for t in tr[first:st]))
    meas["psetup_writes_by_class"] = wr_ps_cls
    return meas


def probe_row(model, op, P, pv, scratch):
    sys.path.insert(0, str(ROOT / "model"))
    import m1
    L = next(x for x in m1.LAYERS[model] if x["op"] == op)
    m = model_terms(L, P)
    r = m["row"]
    assert r["t_cmd"] == pv["pred"], ("model at HEAD != frozen prediction", r["t_cmd"], pv["pred"])
    raw = run_rtl(L, P, scratch / f"{model}_op{op}_P{P}.json")
    assert raw["mismatches"] == 0
    me = analyse(raw["trace"], raw["stamp_addr"], m)
    assert me["bus_stamp"] == raw["stamps"][0] == pv["meas"], (me["bus_stamp"], raw["stamps"], pv["meas"])
    d = dict(setup=r["t_desc"] + r["t_wload"] + r["t_pload"] - me["setup"],
             compute=r["t_compute"] - me["compute"], tail=r["t_flush"] - me["tail"])
    # compute split; the four terms sum to d["compute"] exactly
    dc = dict(psetup=m["n_pix"] - me["psetup_cycles"], reads=m["reads"] - me["issue_reads"],
              writes_in_psetup=me["psetup_writes"], writes_after_compute=me["writes_after_compute"],
              writes_other=m["writes"] - me["issue_writes"] - me["psetup_writes"] - me["writes_after_compute"],
              slack=m["slack"] - me["issue_idle"])
    assert sum(dc.values()) == d["compute"], (dc, d)
    dev = pv["pred"] - pv["meas"]
    reg = pv["registered_bias_pct"] * pv["meas"] / 100.0
    lim = pv["registered_bias_pct"] + MARGIN_PP                    # the deviations.json rule
    byc = me["psetup_writes_by_class"]
    tie, nontie = byc["tie"], byc["port"] + byc["requant"]
    pct = lambda c: round(100.0 * (dev - c) / pv["meas"], 3)      # error left if c cycles were not predicted
    verdict = lambda c: "fully" if pct(c) <= lim else ("partly" if c > 0 else "not at all")
    parts = dict(tie_writes_in_psetup=tie, nontie_writes_in_psetup=nontie,
                 requant_hidden_psetup=dc["slack"],                 # the registered (DESIGN 7.4) mechanism
                 flush=d["tail"] + dc["writes_after_compute"] + d["setup"],
                 other=dc["psetup"] + dc["reads"] + dc["writes_other"])
    assert sum(parts.values()) == dev, (parts, dev)
    return dict(model=model, op=op, P=P, shape={k: L[k] for k in ("H", "W", "C", "O", "KH", "SH", "PH")},
                pred=pv["pred"], meas=pv["meas"], deviation=dev, err_pct=pv["err_pct"],
                registered_bias_pct=pv["registered_bias_pct"], registered_cycles=round(reg, 1),
                excess_over_registered=round(dev - reg, 1),
                model_terms=dict(t_desc=r["t_desc"], t_wload=r["t_wload"], t_pload=r["t_pload"], t_compute=r["t_compute"],
                           t_flush=r["t_flush"], n_pix=m["n_pix"], tiles_per_pix=m["n_tiles"], NV=m["NV"],
                           reads=m["reads"], writes=m["writes"], slack=m["slack"], tie_tiles=m["tie_tiles"],
                           pixel_class_last_tile=m["pixel_class_counts"]),
                measured=me, delta_by_phase=d, delta_compute_split=dc,
                deviation_parts=parts, tie_explained=tie, writes_in_psetup=me["psetup_writes"],
                err_pct_without_tie=pct(tie), err_pct_without_psetup_writes=pct(tie + nontie), rule_limit_pct=lim,
                verdict_tie=verdict(tie), verdict_psetup_writes=verdict(tie + nontie), bus_stamp=me["bus_stamp"])


def main():
    dv = json.loads((ROOT / "results/deviations.json").read_text())["root_cause_A"]
    ex = {(e["model"], e["op"], e["P"]): e for e in dv["exceeding_rows"]}
    assert set(ex) == set(ROWS), sorted(ex)
    scratch = ROOT / "build" / "tie_probe"
    scratch.mkdir(parents=True, exist_ok=True)
    out = []
    for model, op, P in ROWS:
        res = probe_row(model, op, P, ex[(model, op, P)], scratch)
        print(f"{model} op{op} P{P}: pred {res['pred']} meas {res['meas']} dev {res['deviation']} "
              f"parts {res['deviation_parts']} -> tie {res['verdict_tie']}, psetup writes {res['verdict_psetup_writes']}", flush=True)
        out.append(res)
    import cocotb as cb
    env = dict(python=platform.python_version(), platform=platform.platform(), cocotb=cb.__version__,
               verilator=subprocess.run(["verilator", "--version"], capture_output=True, text=True).stdout.strip())
    doc = dict(generated_by="experiments/probe_tie.py", commit=git("rev-parse", "HEAD"),
               tree_dirty=bool(git("status", "--porcelain")), date=datetime.now(timezone.utc).isoformat(timespec="seconds"),
               environment=env, seed=1,
               source="frozen predictions and measured STAMP: results/deviations.json root_cause_A.exceeding_rows "
                      "(results/m6_pred_vs_meas.csv); RTL trace: rtl/accel at commit, one command of the layer shape",
               rule="tie_explained = writes granted in a PSETUP cycle whose pending write belongs to a tie tile "
                    "(lam*(R+W) == max(NV, ceil((NV+lam+3)/2))) of the previous pixel; deviation_parts sum to pred - meas; "
                    "verdict fully if (pred - meas - explained)/meas <= registered bias + margin (the deviations.json rule), "
                    "partly if explained > 0, else not at all",
               rows=out)
    (ROOT / "results/m6_tie_probe.json").write_text(json.dumps(doc, indent=2) + "\n")
    print("results/m6_tie_probe.json written")


if __name__ == "__main__":
    main()
