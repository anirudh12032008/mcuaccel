"""Random layer generator (DESIGN §10.2) and memory-image builder shared by the backend tests."""
from __future__ import annotations

import os
from array import array

import numpy as np
from mcuaccel_backend.layers import Layer
from mcuaccel_backend.split import build_commands, write_image
from mcugrad.quant import int8ops

FULL = os.environ.get("MCUACCEL_SHAPES") == "full"   # §10.2 ranges; default is shrunk for speed


def gen_layer(seed):
    """-> (Layer, P, x_align, y_align, x bytes). Same seed, same layer."""
    r = np.random.default_rng(seed)
    ri = lambda lo, hi: int(r.integers(lo, hi + 1))      # noqa: E731  inclusive
    hw, cmax, omax, kmax = (12, 20, 40, 300) if FULL else (6, 10, 20, 60)
    u = r.random()
    mode = "conv" if u < .5 else "dw" if u < .8 else "fc"
    KH = KW = SH = SW = 1
    PH = PW = 0
    if mode == "fc":
        N, C, O = ri(1, 3), ri(1, kmax), ri(1, omax)
        H, W = 1, N
    else:
        KH, KW, SH, SW = ri(1, 5), ri(1, 5), ri(1, 3), ri(1, 3)
        PH, PW = ri(0, KH - 1), ri(0, KW - 1)
        H, W = max(ri(1, hw), KH - 2 * PH), max(ri(1, hw), KW - 2 * PW)
        if mode == "dw":
            C = 4 * ri(1, 8 if FULL else 4)
            O = C
        else:
            C, O = ri(1, cmax), ri(1, omax)
            if r.random() < .05:                          # force splits
                KH = KW = C = 1
                PH = PW = 0
                H, W, O = ri(1, 2), ri(1, 2), ri(513, 600)
    Cg = 1 if mode == "dw" else C
    K = KH * KW * Cg
    w = array("b", r.integers(-128, 128, O * K).astype(np.int8).tobytes())
    lim = (1 << 31) - 1 - K * 255 * 128
    bias = [int(b) for b in r.integers(-(1 << 20), 1 << 20, O)]
    if r.random() < .05:
        bias = [lim * int(r.choice([-1, 1])) for _ in range(O)]
    mult = [int(m) for m in (r.integers(1 << 30, 1 << 31, O) if r.random() < .85
                             else r.integers(-(1 << 31), 1 << 31, O))]
    shift = [int(s) for s in (r.integers(-31, 32, O) if r.random() < .3 else r.integers(-12, 3, O))]
    amin, amax = (-128, 127) if r.random() < .5 else sorted(int(v) for v in r.integers(-128, 128, 2))
    L = Layer(mode, H, W, C, O, KH, KW, SH, SW, PH, PW, int(r.integers(-128, 128)),
              int(r.integers(-128, 128)), amin, amax, w, bias, mult, shift)
    P = int(r.choice([1, 2, 4, 8, 16]))
    xa, ya = (0 if mode == "dw" else ri(0, 3)), ri(0, 3)
    x = r.integers(-128, 128, H * W * C).astype(np.int8).tobytes()
    return L, P, xa, ya, x


def reference(L, x):
    xa = array("b", x)
    if L.mode == "fc":
        return int8ops.fc_s8(xa, L.W, L.C, L.weights, L.O, L.bias, L.x_zp, L.y_zp, L.mult,
                             L.shift, L.act_min, L.act_max)
    return int8ops.conv2d_nhwc_s8(xa, 1, L.H, L.W, L.C, L.weights, L.O, L.KH, L.KW, L.bias,
                                  L.SH, L.SW, L.PH, L.PW, C_groups(L), L.x_zp, L.y_zp, L.mult,
                                  L.shift, L.act_min, L.act_max)


def C_groups(L):
    return L.C if L.mode == "dw" else 1


def image(L, P, xa, ya, x, seed=0):
    """Garbage-filled memory with x at 16+xa, y after it (>= 8 guard bytes around), then
    weights / params / descriptors. -> (mem, y_addr, ny, first descriptor addr, cmds)."""
    r = np.random.default_rng(seed + 12345)
    x_addr = 16 + xa
    y_addr = (x_addr + len(x) + 8 + 3) // 4 * 4 + ya
    ny = L.OH * L.OW * L.O
    top = (y_addr + ny + 8 + 3) // 4 * 4
    probe = build_commands(L, P, x_addr, y_addr, top, top, 0, 1)    # sizes only
    wsz = sum(len(c.w_bytes) for c in probe)
    psz = sum(len(c.p_bytes) for c in probe)
    p_addr = top + wsz
    d_addr = p_addr + psz
    cmds = build_commands(L, P, x_addr, y_addr, top, p_addr, 0, d_addr)
    mem = bytearray(r.integers(0, 256, d_addr + 72 * len(cmds) + 16).astype(np.uint8).tobytes())
    mem[x_addr:x_addr + len(x)] = x
    write_image(mem, cmds)
    return mem, y_addr, ny, d_addr, cmds
