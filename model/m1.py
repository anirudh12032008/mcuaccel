"""M1 architecture model (DESIGN section 9, timing = DESIGN section 7). Task T3.4.

Predicts accelerator cycles, bus words and MAC utilisation per accelerator command for the two
networks, three architectures ("os" built, "lb" and "acc" modelled only) and P in {1,2,4,8,16}.
Pure numpy, no RTL. `python model/m1.py --out results/` writes m1_predictions.csv and
m1_summary.json. Equations are kept 1:1 with DESIGN 7.1/7.2/9.2-9.4 (section numbers in comments).

Decisions made here (the task said: decide, document):
- Layer table LAYERS is a literal copy of DESIGN 2; tb/unit/test_m1_model.py parses the op_*
  calls of the two generated model.c files and asserts equality. mg_conv1x1_nhwc_s8 and mg_fc_s8
  calls carry no x_zp, so x_zp of those layers is only checked where the call has it.
- fc is a conv with H = W = 1 (N = 1), C = K, O = M, KH = KW = 1 (DESIGN 5).
- Alignment: every arena offset in both model.c files is a multiple of 4 and the arena is
  16-aligned, so only the network input (cifar op_0, flag from_input) can be misaligned:
  `x_align` = (input address) mod 4, default 0. `y_align` = (output address) mod 4 for the final
  fc (default 0). Y alignment per tile follows DESIGN 7.1: yaddr = Y_BASE + pix*O + ch0 with
  Y_BASE = y_align + o_s, so W_t = ceil(((yaddr mod 4) + NV)/4) is evaluated per pixel and tile.
- a_ky (prime word rule, 7.1): a = (x_base + iy*XROW + ix0*XPIX) mod 4 with the UNCLIPPED
  ix0 = ox*SW - PW (rowA is the address of the virtual kx = 0 pixel; 4*j0 is a multiple of 4).
- Third term of T_tile is ceil((NV + lam + l_mac + 1)/credits) = ceil((NV+lam+3)/2) for the defaults.
- Bus words: bus_rd = 17 + Wwords + 3*NCH + (x words actually read). In "os" x words are re-read
  for every tile, so they are NT * sum_pix R (DESIGN 9.2 writes "sum R"; the per-tile re-read is
  what the datapath does). "acc" reads x once per pixel (sum_pix R). bus_wr = sum W_t + 1 (STAMP).
- util = macs / (4P * t_compute), macs = in-bounds MACs of the command.
- Command splitting per 6.4: conv NCH_max = min(512, P*floor(512/ROWS_PER_TILE)), dw
  4*floor(512/(KH*KW)). A split command pays DESC/WLOAD/PLOAD/FLUSH/STAMP again.
- Chains: maximal runs of consecutive accelerated ops of LAYERS (any software op breaks a chain).
  T_chain = sum_cmd (T_cmd + lam + 1) + 2 (7.2). Accelerated model_run =
  sum sw ops (sw_cycles, 0 if unmeasured) + sum_chains (T_chain + t_drv_chain) + patches*t_drv_patch.
  Patched fields per call (6.5): X_ORIGIN of every command of a layer that reads the network
  input (cifar op_0), Y_BASE of every command of a layer that writes `output` (final fc).
- predict() returns accelerator commands only; its sw_cycles argument is accepted for API parity
  and unused there (software ops are not commands); summarize() consumes sw_cycles.
- Saturation width (SPEC C5): smallest P whose total (accelerator + software + driver) is
  <= 1.10 * total(P=16). With sw unmeasured the software term is 0, which makes P* >= the value
  with software included (a constant added to every P only loosens the 10 % test).

Driver constants (DESIGN 9.5). PicoRV32 CPI: load 5, store 5, ALU 3, branch 3 not taken / 5
taken, jal 3, jalr 6. Assumed -O2 rv32im sequence for `mcuaccel_run(first)` as seen from the
caller, counting only what is NOT overlapped with the accelerator:
    caller:  lui a0,%hi(ad); addi a0,a0,%lo(ad); jal mcuaccel_run      alu alu jal      = 3+3+3
    callee:  lui a5,0x30000; sw a0,0x10(a5)  (DESC_ADDR)                alu store        = 3+5
             li a4,1;       sw a4,0x08(a5)  (CTRL.START)                alu store        = 3+5
    poll:    loop = lw a4,0xc(a5); andi a5,a4,1; bnez a5,poll           load alu br_t    = 13/iter
             (overlapped with the accelerator; the non-overlapped part is the expected phase
              wait after DONE, half an iteration = 6, then the final iteration)
             last iteration: lw, andi, bnez not taken                   load alu br_nt   = 5+3+3
    err:     andi a4,a4,4; bnez a4,err (not taken)                      alu br_nt        = 3+3
    return:  ret (jalr)                                                 jalr             = 6
    t_drv_chain = 9 + 16 + 6 + 11 + 6 + 6 = 54 cycles.
    t_drv_patch = one patched descriptor field: add to form the value, reload descriptor
    pointer, sw: alu alu store = 3+3+5 = 11 cycles.
These are estimates, refined in M1 proper from the compiled driver.

Measured inputs (optional, absence tolerated; provenance recorded in the summary):
  results/baseline_<model>.json         {"mcugrad_result": {"cycles_median": ...}, "x_align": ...}
  results/baseline_profile_<model>.json {"ops": [{"op": i, "cycles_per_inference": ...}, ...]}
  results/baseline_overhead.json        {"cycles_median": 120}  bench_main window, both builds
  results/driver_overhead.json          {"drv_chain": .., "drv_patch": ..}  measured on the stub
When driver_overhead.json is present its drv_chain / drv_patch replace the CPI estimate above
(provenance "measured on stub"); otherwise the estimate is used ("CPI estimate").
"""
import argparse
import csv
import datetime
import json
import math
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PS = (1, 2, 4, 8, 16)
ARCHS = ("os", "lb", "acc")

# ---------------------------------------------------------------- layer table (DESIGN section 2)


def _conv(op, H, W, C, O, K, S, P, zp, **kw):
    return dict(op=op, kind="conv", H=H, W=W, C=C, O=O, KH=K, KW=K, SH=S, SW=S, PH=P, PW=P,
                groups=1, x_zp=zp, **kw)


def _dw(op, H, W, C, K, S, P, zp):
    return dict(op=op, kind="dw", H=H, W=W, C=C, O=C, KH=K, KW=K, SH=S, SW=S, PH=P, PW=P,
                groups=C, x_zp=zp)


def _fc(op, K, M, zp, **kw):
    return dict(op=op, kind="fc", H=1, W=1, C=K, O=M, KH=1, KW=1, SH=1, SW=1, PH=0, PW=0,
                groups=1, x_zp=zp, **kw)


def _pool(op, kind, H, W, C, K, S):  # maxpool / avgpool, square window, no pad
    return dict(op=op, kind=kind, H=H, W=W, C=C, O=C, KH=K, KW=K, SH=S, SW=S, PH=0, PW=0)


def _tr(op, C, H, W):  # transpose_nchw_to_nhwc(x, N=1, C, H, W)
    return dict(op=op, kind="transpose", H=H, W=W, C=C, O=C)


LAYERS = {
    "mnist_cnn": [
        _tr(0, 1, 28, 28),
        _conv(1, 28, 28, 1, 8, 3, 1, 1, -95),
        _pool(2, "maxpool", 28, 28, 8, 2, 2),
        _conv(3, 14, 14, 8, 16, 3, 1, 1, -128),
        _pool(4, "maxpool", 14, 14, 16, 2, 2),
        _tr(5, 49, 16, 1),
        _fc(7, 784, 10, -128, writes_output=True),
    ],
    "cifar_mobilenet": [
        _conv(0, 32, 32, 3, 16, 3, 1, 1, -7, from_input=True),
        _dw(1, 32, 32, 16, 3, 2, 1, -128),
        _conv(2, 16, 16, 16, 32, 1, 1, 0, -128),
        _dw(3, 16, 16, 32, 3, 1, 1, -128),
        _conv(4, 16, 16, 32, 32, 1, 1, 0, -128),
        _dw(5, 16, 16, 32, 3, 2, 1, -128),
        _conv(6, 8, 8, 32, 64, 1, 1, 0, -128),
        _dw(7, 8, 8, 64, 3, 1, 1, -128),
        _conv(8, 8, 8, 64, 64, 1, 1, 0, -128),
        _dw(9, 8, 8, 64, 3, 2, 1, -128),
        _conv(10, 4, 4, 64, 128, 1, 1, 0, -128),
        _dw(11, 4, 4, 128, 3, 1, 1, -128),
        _conv(12, 4, 4, 128, 128, 1, 1, 0, -128),
        _pool(13, "avgpool", 4, 4, 128, 4, 4),
        _fc(15, 128, 10, -128, writes_output=True),
    ],
}
ACCEL_KINDS = ("conv", "dw", "fc")


def out_hw(L):
    return ((L["H"] + 2 * L["PH"] - L["KH"]) // L["SH"] + 1,
            (L["W"] + 2 * L["PW"] - L["KW"]) // L["SW"] + 1)


# ---------------------------------------------------------------- parameters and driver constants

CPI = dict(load=5, store=5, alu=3, br_nt=3, br_t=5, jal=3, jalr=6)
_POLL_ITER = CPI["load"] + CPI["alu"] + CPI["br_t"]
DRV_CHAIN_SEQ = (  # (instruction class, count): see the module docstring
    ("alu", 2), ("jal", 1),                       # caller: a0 = &desc, call
    ("alu", 1), ("store", 1), ("alu", 1), ("store", 1),  # DESC_ADDR, CTRL.START
    ("wait", 0),                                  # half a poll iteration (expected phase)
    ("load", 1), ("alu", 1), ("br_nt", 1),        # last poll iteration, falls through
    ("alu", 1), ("br_nt", 1),                     # ERR check
    ("jalr", 1))                                  # ret
DRV_PATCH_SEQ = (("alu", 2), ("store", 1))


def _seq_cycles(seq):
    return sum(_POLL_ITER // 2 if k == "wait" else CPI[k] * n for k, n in seq)


T_DRV_CHAIN = _seq_cycles(DRV_CHAIN_SEQ)   # 54
T_DRV_PATCH = _seq_cycles(DRV_PATCH_SEQ)   # 11


@dataclass(frozen=True)
class Cfg:
    lam: int = 1
    credits: int = 2
    l_mac: int = 2
    flush_const: int = 10      # the "10" of FLUSH = 2*lam + 10 + NV_last (includes L_rq = 6)
    x_align: int = 0           # network input address mod 4
    y_align: int = 0           # network output address mod 4
    wbuf_rows: int = 512
    pbuf: int = 512
    t_drv_chain: int = T_DRV_CHAIN
    t_drv_patch: int = T_DRV_PATCH


def cdiv(a, b):
    return -(-a // b)


# ---------------------------------------------------------------- geometry and commands


def _geom(L):
    """Per-pixel clipped window (7.1), arrays of shape (OH,1) / (1,OW)."""
    OH, OW = out_hw(L)
    oy, ox = np.arange(OH)[:, None], np.arange(OW)[None, :]
    iy0, ix0 = oy * L["SH"] - L["PH"], ox * L["SW"] - L["PW"]
    return dict(OH=OH, OW=OW, iy0=iy0, ix0=ix0,
                ky0=np.maximum(0, -iy0), ky1=np.minimum(L["KH"], L["H"] - iy0),
                kx0=np.maximum(0, -ix0), kx1=np.minimum(L["KW"], L["W"] - ix0))


def _rq(L):  # RQ = ceil(RUN_BYTES/4), RUN_BYTES = KW*C
    return cdiv(L["KW"] * L["C"], 4)


def commands(L, P, cfg):
    """Split a layer into commands (o_s, nch) per 6.4."""
    if L["kind"] == "dw":
        per, n = 4 * (cfg.wbuf_rows // (L["KH"] * L["KW"])), L["C"]
    else:
        rpt = L["KH"] * _rq(L)
        if rpt > cfg.wbuf_rows:
            raise ValueError("ROWS_PER_TILE > wbuf_rows: software fallback")
        per, n = P * (cfg.wbuf_rows // rpt), L["O"]
    per = min(per, cfg.pbuf)
    return [(s, min(per, n - s)) for s in range(0, n, per)]


def _tiles(L, P, nch):
    step = 4 if L["kind"] == "dw" else P
    return [(t * step, min(step, nch - t * step)) for t in range(cdiv(nch, step))]


def _R(L, g, x_base):
    """R(pix), words read per pixel and tile (7.1 'Per-row word count')."""
    if L["kind"] == "dw":
        return (g["ky1"] - g["ky0"]) * (g["kx1"] - g["kx0"])
    C = L["C"]
    nq = ((g["kx1"] * C + 3) >> 2) - ((g["kx0"] * C) >> 2)        # j1 - j0
    R = np.zeros((g["OH"], g["OW"]), dtype=np.int64)
    for ky in range(L["KH"]):
        valid = (ky >= g["ky0"]) & (ky < g["ky1"])
        a = (x_base + (g["iy0"] + ky) * L["W"] * C + g["ix0"] * C) % 4   # prime-word rule
        R += valid * (nq + (a != 0))
    return R


def _macs(L, g, nch):
    rows, cols = g["ky1"] - g["ky0"], g["kx1"] - g["kx0"]
    return int((rows * cols).sum()) * nch * (1 if L["kind"] == "dw" else L["C"])


def _mac_cycles(L, g):
    """Per pixel MAC cycles of one tile of a conv/fc: sum over in-bounds ky of (j1 - j0)."""
    C = L["C"]
    return (g["ky1"] - g["ky0"]) * (((g["kx1"] * C + 3) >> 2) - ((g["kx0"] * C) >> 2))


def _wt(L, g, cfg, o_s, ch0, NV):
    """W_t per pixel: ceil(((yaddr mod 4) + NV)/4), yaddr = Y_BASE + pix*O + ch0 (7.1)."""
    OW = g["OW"]
    pix = np.arange(g["OH"])[:, None] * OW + np.arange(OW)[None, :]
    y_base = cfg.y_align + o_s
    return -(-(((y_base + pix * L["O"] + ch0) % 4) + NV) // 4)


def _tile_time(R, W, NV, cfg):
    """T_tile = max(lam*(R+W_t), NV_t, ceil((NV_t+lam+3)/2)) (7.2)."""
    third = cdiv(NV + cfg.lam + cfg.l_mac + 1, cfg.credits)
    return np.maximum(cfg.lam * (R + W), max(NV, third))


# ---------------------------------------------------------------- per-command evaluation


def _cmd_os(L, P, cfg, o_s, nch, tiles_out=None):
    g = _geom(L)
    x_base = cfg.x_align if L.get("from_input") and L["kind"] != "dw" else 0
    R = _R(L, g, x_base)
    T_pix, wr = 1, 0
    for ch0, NV in _tiles(L, P, nch):
        W = _wt(L, g, cfg, o_s, ch0, NV)
        Tt = _tile_time(R, W, NV, cfg)
        if tiles_out is not None:
            tiles_out.append(dict(ch0=ch0, NV=NV, W=W, T=Tt, R=R))
        T_pix = T_pix + Tt
        wr += int(W.sum())
    nt = len(_tiles(L, P, nch))
    return dict(g=g, t_compute=int(np.broadcast_to(T_pix, R.shape).sum()),
                x_words=nt * int(R.sum()), wr=wr)


def _cmd_acc(L, P, cfg, o_s, nch):
    if L["kind"] == "dw":
        return _cmd_os(L, P, cfg, o_s, nch)
    g = _geom(L)
    x_base = cfg.x_align if L.get("from_input") else 0
    R = _R(L, g, x_base)
    tiles = _tiles(L, P, nch)
    sumW = sum(_wt(L, g, cfg, o_s, ch0, NV) for ch0, NV in tiles)
    mac = len(tiles) * _mac_cycles(L, g)
    T_pix = 1 + np.maximum(np.maximum(cfg.lam * (R + sumW), mac), nch)   # sum_t NV_t = nch
    return dict(g=g, t_compute=int(T_pix.sum()), x_words=int(R.sum()), wr=int(sumW.sum()))


def _cmd_lb(L, P, cfg, o_s, nch):
    g = _geom(L)
    OH, OW = g["OH"], g["OW"]
    rows = (g["ky1"] - g["ky0"])[:, 0]                               # in-bounds ky count per oy
    if L["kind"] == "dw":
        t, words, wr = 0, 0, 0
        for ox in range(0, OW, P):
            Pb = min(P, OW - ox)
            ixf = ox * L["SW"] - L["PW"]
            ixl = (ox + Pb - 1) * L["SW"] - L["PW"]
            ncols = min(L["W"], ixl + L["KW"]) - max(0, ixf)
            w_blk = rows * ncols + Pb                                # words incl. Pb writes
            t += int((1 + np.maximum(cfg.lam * w_blk, 4 * Pb)).sum())
            words += int((rows * ncols).sum())
            wr += Pb * OH
        ng = nch // 4
        return dict(g=g, t_compute=ng * t, x_words=ng * words, wr=ng * wr)
    C = L["C"]
    row_words = cdiv(L["W"] * C, 4) + 1
    words_oy = rows * row_words                                      # streamed once per (oy, t)
    mac_oy = _mac_cycles(L, g).sum(axis=1)                            # sum_ox sum_ky (j1-j0)
    t_total, wr, nx = 0, 0, 0
    for ch0, NV in _tiles(L, P, nch):
        Wsum = _wt(L, g, cfg, o_s, ch0, NV).sum(axis=1)              # sum_ox W_t per oy
        T = np.maximum(np.maximum(cfg.lam * (words_oy + Wsum), mac_oy), OW * NV) + 1
        t_total += int(T.sum())
        wr += int(Wsum.sum())
        nx += int(words_oy.sum())
    return dict(g=g, t_compute=t_total, x_words=nx, wr=wr)


_ARCH = {"os": _cmd_os, "lb": _cmd_lb, "acc": _cmd_acc}

COLUMNS = ("model op cmd kind arch P lam H W C O KH KW SH SW PH PW o_s nch macs t_desc t_wload "
           "t_pload t_compute t_flush t_cmd bus_rd bus_wr util").split()


def _command_row(model, L, arch, P, cfg, ci, o_s, nch):
    r = _ARCH[arch](L, P, cfg, o_s, nch)
    lam = cfg.lam
    dw = L["kind"] == "dw"
    nt = cdiv(nch, 4 if dw else P)
    wwords = nt * L["KH"] * L["KW"] if dw else nt * L["KH"] * _rq(L) * P      # 7.2 Wwords
    nv_last = 4 if dw else nch - (nt - 1) * P
    t_desc, t_wload, t_pload = 17 * lam + 1, lam * wwords + 1, 3 * lam * nch + 1
    t_flush = 2 * lam + cfg.flush_const + nv_last
    macs = _macs(L, r["g"], nch)
    t_cmd = t_desc + t_wload + t_pload + r["t_compute"] + t_flush        # = STAMP
    row = dict(model=model, op=L["op"], cmd=ci, kind=L["kind"], arch=arch, P=P, lam=lam,
               o_s=o_s, nch=nch, macs=macs, t_desc=t_desc, t_wload=t_wload, t_pload=t_pload,
               t_compute=r["t_compute"], t_flush=t_flush, t_cmd=t_cmd,
               bus_rd=17 + wwords + 3 * nch + r["x_words"], bus_wr=r["wr"] + 1,
               util=macs / (4 * P * r["t_compute"]))
    row.update({k: L[k] for k in "H W C O KH KW SH SW PH PW".split()})
    return row


def predict(model_name, arch, P, lam=1, sw_cycles=None, x_align=0, **kw):
    """One dict per accelerator command of `model_name` (sw_cycles unused here, see docstring)."""
    cfg = Cfg(lam=lam, x_align=x_align, **kw)
    return [_command_row(model_name, L, arch, P, cfg, ci, o_s, nch)
            for L in LAYERS[model_name] if L["kind"] in ACCEL_KINDS
            for ci, (o_s, nch) in enumerate(commands(L, P, cfg))]


# ---------------------------------------------------------------- baselines, summary


def _json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def load_baseline(model, results_dir=ROOT / "results"):
    """(cycles_median or None, {op: cycles_per_inference}, x_align or None, info dict).
    Real JSON shapes of experiments/run_baseline.py; missing or malformed files give Nones."""
    d = Path(results_dir)
    base = _json(d / f"baseline_{model}.json") or {}
    prof = _json(d / f"baseline_profile_{model}.json") or {}
    try:
        cyc = int(base["mcugrad_result"]["cycles_median"])
    except (KeyError, TypeError, ValueError):
        cyc = None
    try:
        ops = {int(o["op"]): float(o["cycles_per_inference"]) for o in prof["ops"]}
    except (KeyError, TypeError, ValueError):
        ops = {}
    info = dict(baseline=dict(commit=base.get("commit"), images=base.get("images"),
                              runs=base.get("runs"), date=base.get("date")) if base else None,
                profile=dict(commit=prof.get("commit"), images=prof.get("images"),
                             runs=prof.get("runs"), date=prof.get("date")) if prof else None)
    return cyc, ops, base.get("x_align"), info


def load_window(results_dir=ROOT / "results"):
    """bench_main window overhead (cycles, identical in both builds) or 0 when unmeasured."""
    j = _json(Path(results_dir) / "baseline_overhead.json")
    try:
        return int(j["cycles_median"]), True
    except (TypeError, KeyError, ValueError):
        return 0, False


def load_driver(results_dir=ROOT / "results"):
    """(t_drv_chain, t_drv_patch, provenance). Measured on the stub when results/driver_overhead.json
    exists, else the CPI estimate."""
    j = _json(Path(results_dir) / "driver_overhead.json")
    try:
        return int(j["drv_chain"]), int(j["drv_patch"]), "measured on stub"
    except (TypeError, KeyError, ValueError):
        return T_DRV_CHAIN, T_DRV_PATCH, "CPI estimate"


def p_star(totals):
    """SPEC C5: smallest P with total <= 1.10 * total(P=16); totals = {P: cycles}."""
    return min(p for p in totals if totals[p] <= 1.10 * totals[16])


def summarize(model_name, arch, P, cfg=None, sw_cycles=None, baseline=None, rows=None, window=0):
    """DESIGN 9.6 summary entry for one model x arch x P."""
    cfg = cfg or Cfg()
    rows = rows if rows is not None else predict(model_name, arch, P, **asdict(cfg))
    sw_cycles = sw_cycles or {}
    per_op, chains, cur = [], [], None
    for L in LAYERS[model_name]:
        if L["kind"] not in ACCEL_KINDS:
            cur = None
            per_op.append(dict(op=L["op"], kind=L["kind"], where="sw",
                               cycles=sw_cycles.get(L["op"])))
            continue
        cmds = [r for r in rows if r["op"] == L["op"]]
        if cur is None:
            cur = []
            chains.append(cur)
        cur.extend(cmds)
        per_op.append(dict(op=L["op"], kind=L["kind"], where="accel",
                           cycles=sum(r["t_cmd"] + cfg.lam + 1 for r in cmds)))
    accel = sum(sum(r["t_cmd"] + cfg.lam + 1 for r in c) + 2 for c in chains)
    sw = sum(v["cycles"] or 0 for v in per_op if v["where"] == "sw")
    patches = sum(1 for L in LAYERS[model_name] if L["kind"] in ACCEL_KINDS
                  for r in rows if r["op"] == L["op"]
                  for f in ("from_input", "writes_output") if L.get(f))
    drv = len(chains) * cfg.t_drv_chain + patches * cfg.t_drv_patch
    total = accel + sw + drv + window
    tc = sum(r["t_compute"] for r in rows)
    return dict(accel_cycles=accel, sum_stamp=sum(r["t_cmd"] for r in rows), sw_cycles=sw,
                driver_cycles=drv, bench_window_cycles=window,
                accel_plus_driver_cycles=accel + drv, predicted_model_run=total,
                n_chains=len(chains), n_commands=len(rows), n_patches=patches,
                sw_ops=[v["op"] for v in per_op if v["where"] == "sw"],
                sw_complete=all(v["cycles"] is not None for v in per_op if v["where"] == "sw"),
                baseline_cycles=baseline,
                predicted_speedup=(baseline / total) if baseline else None,
                macs=sum(r["macs"] for r in rows), bus_rd=sum(r["bus_rd"] for r in rows),
                bus_wr=sum(r["bus_wr"] for r in rows),
                util=sum(r["macs"] for r in rows) / (4 * P * tc), ops=per_op)


def _commit():
    try:
        git = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True,
                                        check=True).stdout.strip()
        return git("rev-parse", "HEAD") + (" (dirty)" if git("status", "--porcelain") else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def run_all(out_dir, cfg=None, results_dir=None):
    """Write m1_predictions.csv and m1_summary.json; return the summary dict. Measured driver
    constants replace the CPI estimate unless `cfg` is passed explicitly."""
    out_dir = Path(out_dir)
    results_dir = Path(results_dir or out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    chain, patch, drv_prov = load_driver(results_dir)
    if cfg is None:
        cfg = Cfg(t_drv_chain=chain, t_drv_patch=patch)
    else:
        drv_prov = "explicit Cfg" if (cfg.t_drv_chain, cfg.t_drv_patch) != (chain, patch) else drv_prov
    window, window_ok = load_window(results_dir)
    all_rows, models, measured = [], {}, {}
    for m in LAYERS:
        base, ops, xa, info = load_baseline(m, results_dir)
        sw_ops = [L["op"] for L in LAYERS[m] if L["kind"] not in ACCEL_KINDS]
        measured[m] = dict(baseline_cycles="measured" if base else "absent (speedup null)",
                           sw_cycles=("measured (profile)" if sw_ops and all(o in ops for o in sw_ops)
                                      else "absent: 0 used"),
                           x_align_from_baseline=xa, **info)
        models[m] = dict(baseline_cycles=base, archs={})
        for arch in ARCHS:
            ent, tot, stamp = {}, {}, {}
            for P in PS:
                rows = predict(m, arch, P, **asdict(cfg))
                all_rows += rows
                ent[str(P)] = summarize(m, arch, P, cfg, ops, base, rows, window)
                tot[P] = ent[str(P)]["predicted_model_run"]
                stamp[P] = ent[str(P)]["sum_stamp"]
            models[m]["archs"][arch] = dict(P=ent, p_star=p_star(stamp),
                                            p_star_end_to_end=p_star(tot))
    with open(out_dir / "m1_predictions.csv", "w", newline="") as f:
        w = csv.DictWriter(f, COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in all_rows:
            w.writerow({k: (f"{v:.4f}" if k == "util" else v) for k, v in r.items()})
    summary = dict(commit=_commit(), date=datetime.date.today().isoformat(),
                   parameters=asdict(cfg) | {"Ps": list(PS), "cpi": CPI,
                                             "drv_chain_seq": DRV_CHAIN_SEQ,
                                             "drv_patch_seq": DRV_PATCH_SEQ},
                   inputs=dict(accelerator="modelled (DESIGN 7)", driver=drv_prov,
                               bench_window_cycles=window if window_ok else "absent: 0 used",
                               p_star_definition="SPEC C5: smallest P with sum(STAMP) <= 1.10 * "
                                                 "sum(STAMP)(P=16), accelerator only; "
                                                 "p_star_end_to_end uses predicted_model_run",
                               layers="DESIGN 2 table, checked against model.c by test",
                               per_model=measured),
                   models=models)
    (out_dir / "m1_summary.json").write_text(json.dumps(summary, indent=1))
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(ROOT / "results"))
    ap.add_argument("--results", default=None, help="dir with baseline/driver JSON (default: --out)")
    ap.add_argument("--x-align", type=int, default=None, help="default: x_align of the baseline JSON (0)")
    a = ap.parse_args()
    rd = Path(a.results or a.out)
    xa = a.x_align
    if xa is None:   # network-input alignment of the measured firmware (cifar op_0 reads it)
        xa = load_baseline("cifar_mobilenet", rd)[2] or 0
    c, pt, _ = load_driver(rd)
    s = run_all(a.out, Cfg(x_align=xa, t_drv_chain=c, t_drv_patch=pt), rd)
    for m, md in s["models"].items():
        for arch, ad in md["archs"].items():
            print(m, arch, "P*=", ad["p_star"],
                  {p: ad["P"][p]["accel_cycles"] for p in ad["P"]})
