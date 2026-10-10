"""Layer generation and RAM-image construction for the accelerator-level tests (DESIGN 10.2).

Reuses backend/tests/helpers.py (gen_layer, reference) and the backend packer/splitter. Adds:
- `make_layer`/`gen_next`/`gen_chain`: layers with a given shape, for dependent chains
  (the output of command j is the input of command j+1);
- `build_image`: places one or more layers in a RAM image (any base; base 0 gives an image the
  Python executor can run), with the x/y guard rules of DESIGN 6.4, garbage everywhere else,
  the expected final image (garbage unchanged, y regions = int8ops reference, STAMP words ignored).
Pure Python (numpy), no cocotb: importable by pytest and by the cocotb process alike.
"""
from __future__ import annotations

import struct
import sys
from array import array
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "backend" / "tests", ROOT / "backend", ROOT / "third_party" / "mcugrad"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helpers import FULL, gen_layer, reference  # noqa: E402  (backend/tests/helpers.py)
from mcuaccel_backend.layers import Layer, check  # noqa: E402
from mcuaccel_backend.split import build_commands, split_plan  # noqa: E402

RAM_BASE = 0x8000_0000
RAM_SIZE = 1 << 20
GUARD = 8


def al4(v):
    return (v + 3) & ~3


@dataclass
class Item:
    """One layer to place. x None: input is the previous item's output (dependent chain)."""
    L: Layer
    x: bytes | None
    xa: int = 0
    ya: int = 0


@dataclass
class Placed:
    L: Layer
    x_addr: int
    y_addr: int
    ny: int
    d_addr: int
    ncmds: int
    ref: bytes


@dataclass
class Image:
    base: int
    mem: bytearray
    expected: bytearray
    placed: list
    d_first: int
    ncmds: int
    stamp_addrs: list = field(default_factory=list)
    max_cycles: int = 0

    def off(self, addr):
        return addr - self.base

    def y_ranges(self):
        return [(self.off(p.y_addr), p.ny) for p in self.placed]


# ---------------------------------------------------------------- layer generation
def make_layer(r, mode, H, W, C, O, KH, KW, SH, SW, PH, PW):
    """Random parameters with the DESIGN 10.2 distributions for a layer of the given shape."""
    ri = lambda lo, hi: int(r.integers(lo, hi + 1))      # noqa: E731
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
    L = Layer(mode, H, W, C, O, KH, KW, SH, SW, PH, PW, ri(-128, 127), ri(-128, 127),
              amin, amax, w, bias, mult, shift)
    assert check(L) is True, check(L)
    return L


def gen_next(r, H, W, C):
    """A layer consuming an (H, W, C) NHWC tensor."""
    ri = lambda lo, hi: int(r.integers(lo, hi + 1))      # noqa: E731
    u = r.random()
    omax = 24 if FULL else 12
    if u < .3 and C % 4 == 0 and C <= 32:
        mode = "dw"
    elif u < .4 and H == 1:
        mode = "fc"
    else:
        mode = "conv"
    if mode == "fc":
        return make_layer(r, "fc", 1, W, C, ri(1, omax), 1, 1, 1, 1, 0, 0)
    for _ in range(50):
        KH, KW, SH, SW = ri(1, 3), ri(1, 3), ri(1, 2), ri(1, 2)
        PH, PW = ri(0, KH - 1), ri(0, KW - 1)
        if H + 2 * PH >= KH and W + 2 * PW >= KW:
            break
    else:
        KH = KW = SH = SW = 1
        PH = PW = 0
    return make_layer(r, mode, H, W, C, C if mode == "dw" else ri(1, omax), KH, KW, SH, SW, PH, PW)


def gen_chain(seed, n=None, dependent=None):
    """2..4 random commands (Items). Dependent chains feed each layer's y to the next one's x."""
    r = np.random.default_rng(seed + 99991)
    n = n or int(r.integers(2, 5))
    dep = (r.random() < .5) if dependent is None else dependent
    items = []
    for j in range(n):
        if j == 0 or not dep:
            L, _, xa, ya, x = gen_layer(seed * 8 + j + 100_000)
            items.append(Item(L, x, xa, ya))
        else:
            p = items[-1].L
            L = gen_next(r, p.OH, p.OW, p.O)
            if L.mode == "dw":
                items[-1].ya = 0                 # dw needs a 4-byte aligned input
            items.append(Item(L, None, 0, int(r.integers(0, 4))))
    return items


def est_cycles_bound(L, P):
    """Very generous upper bound of the cycles of one layer (for hang detection only)."""
    pixels = L.OH * L.OW
    rq = 0 if L.mode == "dw" else (L.KW * L.C + 3) // 4
    nt = (L.O + 3) // 4 if L.mode == "dw" else (L.O + P - 1) // P
    per_tile = L.KH * ((L.KW if L.mode == "dw" else rq + 1)) + 2 + (4 if L.mode == "dw" else P)
    words = 17 + 3 * L.O + 512 * P * len(split_plan(L, P)) + pixels * nt * per_tile
    return 40 * words + 5000


# ---------------------------------------------------------------- image construction
def build_image(items, P, seed, base=RAM_BASE, size=RAM_SIZE):
    r = np.random.default_rng(seed + 12345)
    cur = 16                                             # >= GUARD bytes below the first tensor
    lay, prev_y, prev_ref = [], None, None
    for it in items:
        if it.x is not None:
            x_off = al4(cur) + it.xa
            cur = x_off + len(it.x) + GUARD
            xdata = it.x
        else:
            x_off, xdata = prev_y, prev_ref
            assert len(xdata) == it.L.H * it.L.W * it.L.C, "dependent chain shape mismatch"
        y_off = al4(cur) + it.ya
        ny = it.L.OH * it.L.OW * it.L.O
        cur = y_off + ny + GUARD
        ref = reference(it.L, xdata).tobytes()
        lay.append((it, x_off, xdata, y_off, ny, ref, it.x is not None))
        prev_y, prev_ref = y_off, ref
    top = al4(cur)
    sizes = []
    for it, x_off, _, y_off, _, _, _ in lay:
        probe = build_commands(it.L, P, base + x_off, base + y_off, base + top, base + top, 0, base + 1)
        sizes.append((sum(len(c.w_bytes) for c in probe), sum(len(c.p_bytes) for c in probe), len(probe)))
    w_cur = top
    p_cur = w_cur + sum(s[0] for s in sizes)
    d_cur = p_cur + sum(s[1] for s in sizes)
    end = d_cur + 72 * sum(s[2] for s in sizes) + 16
    assert end + GUARD < size, f"layer set does not fit in {size} bytes of RAM ({end})"
    mem = bytearray(size)
    mem[:end + 64] = r.integers(0, 256, min(end + 64, size)).astype(np.uint8).tobytes()
    placed, stamps, bound, ncmds = [], [], 0, 0
    d_first = base + d_cur
    for i, ((it, x_off, xdata, y_off, ny, ref, own), (wsz, psz, ns)) in enumerate(zip(lay, sizes)):
        nxt = base + d_cur + 72 * ns if i + 1 < len(lay) else 0
        cmds = build_commands(it.L, P, base + x_off, base + y_off, base + w_cur, base + p_cur,
                              nxt, base + d_cur)
        if own:
            mem[x_off:x_off + len(xdata)] = xdata
        for c in cmds:
            o = c.w_addr - base
            mem[o:o + len(c.w_bytes)] = c.w_bytes
            o = c.p_addr - base
            mem[o:o + len(c.p_bytes)] = c.p_bytes
            o = c.d_addr - base
            mem[o:o + 72] = struct.pack("<18I", *c.desc)
            stamps.append(c.d_addr + 68)
        placed.append(Placed(it.L, base + x_off, base + y_off, ny, base + d_cur, ns, ref))
        bound += est_cycles_bound(it.L, P)
        ncmds += ns
        w_cur += wsz
        p_cur += psz
        d_cur += 72 * ns
    expected = bytearray(mem)
    for p in placed:
        o = p.y_addr - base
        expected[o:o + p.ny] = p.ref
    return Image(base, mem, expected, placed, d_first, ncmds, stamps, bound)


def exec_stats(items, P, seed):
    """Executor (backend.exec) per-command stats on a base-0 copy of the image; also returns
    whether its output equals the expected image (sanity)."""
    from mcuaccel_backend.exec import run_descriptor
    img = build_image(items, P, seed, base=0)
    st = run_descriptor(img.mem, img.d_first, P, stats=True)
    return st
