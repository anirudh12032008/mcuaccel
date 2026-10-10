"""Self-check of tb/common/models.py against int8ops / the executor (no RTL needed)."""
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend/tests"))
from common import models as M  # noqa: E402
from mcugrad.quant import int8ops  # noqa: E402

I32 = (-(1 << 31), -1, 0, 1, (1 << 31) - 1)
MULTS = (-(1 << 31), -1, 0, 1 << 30, (1 << 31) - 1)
SHIFTS = (-31, -1, 0, 1, 31)


def test_requant_model_equals_int8ops():
    rng = random.Random(1)
    for _ in range(10_000):
        acc = rng.choice(I32) if rng.random() < .2 else rng.randint(-(1 << 31), (1 << 31) - 1)
        bias = rng.randint(-(1 << 20), 1 << 20) if rng.random() < .5 else rng.randint(-(1 << 31), (1 << 31) - 1)
        mult = rng.choice(MULTS) if rng.random() < .2 else rng.randint(-(1 << 31), (1 << 31) - 1)
        sh = rng.randint(-31, 31)
        yzp = rng.randint(-128, 127)
        lo, hi = sorted(rng.randint(-128, 127) for _ in range(2))
        s = M.s32(acc + bias)
        want = int8ops._clamp(int8ops.requant(s, mult, sh) + yzp, lo, hi) & 255
        assert M.requant_out(acc, bias, mult, sh & 63, yzp, lo, hi) == want
        assert M.requant_pipeline(acc, bias, mult, sh & 63, yzp, lo, hi) == want


def test_requant_pipeline_directed_grid():
    for acc in I32:
        for mult in MULTS:
            for sh in SHIFTS:
                for bias in (0, 1, -(1 << 31)):
                    for yzp, lo, hi in ((127, -128, 127), (-128, -128, -128), (-128, 5, 5), (127, -128, 127)):
                        a = M.requant_out(acc, bias, mult, sh & 63, yzp, lo, hi)
                        assert a == M.requant_pipeline(acc, bias, mult, sh & 63, yzp, lo, hi), (acc, mult, sh)
    # the MIN*MIN case saturates to INT32_MAX before rounding
    assert int8ops.srdhm(-(1 << 31), -(1 << 31)) == (1 << 31) - 1
    assert M.requant_out(-(1 << 31), 0, -(1 << 31), 0, 0, -128, 127) == 127


def test_requant_y_zp_does_not_wrap():
    # r = INT32_MAX (mult = 2^31-1, acc = 2^30, shift +1 gives av = 2^31 -> wraps; use acc = 2^30-1)
    r = int8ops.requant((1 << 30) - 1, (1 << 31) - 1, 1)
    assert r > (1 << 29)
    # direct: r + 127 > INT32_MAX needs r near MAX: acc = MIN, mult = MIN -> SRDHM = INT32_MAX
    assert int8ops.requant(-(1 << 31), -(1 << 31), 0) == (1 << 31) - 1
    assert M.requant_out(-(1 << 31), 0, -(1 << 31), 0, 127, -128, 127) == 127   # not -2^31+126 wrapped
    assert M.requant_pipeline(-(1 << 31), 0, -(1 << 31), 0, 127, -128, 127) == 127


def test_mac_model_wrap_and_modes():
    P = 4
    # positive overflow: xs = -255 (x = -128, x_zp = 127), w = -128 -> +32640 per lane per beat
    quad, w = 0x80808080, [0x80808080] * P
    m = M.MacModel(P)
    n, exact = 17_000, 0
    for i in range(n):
        out = m.beat(quad, 0xF, 127, w, 0, i == 0, i == n - 1)
        exact += 4 * 255 * 128
    assert exact > (1 << 31) - 1
    assert out == [M.s32(exact)] * P and out[0] < 0       # wrapped to negative
    # dw uses word 0 only, lane l -> acc l; masked lanes contribute 0
    m = M.MacModel(2)
    out = m.beat(0x04030201, 0b0101, 0, [0x01020304, 0x7F7F7F7F], 1, 1, 1)
    assert out == [1 * 4, 0, 3 * 2, 0]
    # conv with P < 4: only P valid entries
    m = M.MacModel(1)
    out = m.beat(0x01010101, 0xF, 1, [0x05050505], 0, 1, 1)
    assert out == [0] and len(m.acc) == 4
    out = m.beat(0x03030303, 0b1011, 1, [0xFF010203], 0, 0, 1)   # continue: xs=2 on lanes 0,1,3
    assert out == [2 * 3 + 2 * 2 + 2 * -1]


def test_align_model_reconstructs_run():
    rng = random.Random(3)
    mem = bytes(rng.randrange(256) for _ in range(64))
    for a in range(4):
        al = M.AlignModel()
        start = 16 + a
        run = mem[start:start + 20]
        words = [int.from_bytes(mem[16 + 4 * k:20 + 4 * k], "little") for k in range(8)]
        got = b""
        al.clock(1, words[0]) if a else None            # prime
        for j in range(5):
            w = words[j + (1 if a else 0)]
            got += al.quad(w, a).to_bytes(4, "little")
            al.clock(1, w)
        assert got == run


def test_writer_model():
    s = [(0x103, 1, 0), (0x104, 2, 0), (0x105, 3, 1), (0x106, 4, 0), (0x107, 5, 0), (0x108, 6, 1)]
    assert M.writer_words(s) == [(0x100, 0b1000, 1 << 24), (0x104, 0b0011, 2 | 3 << 8),
                                 (0x104, 0b1100, 4 << 16 | 5 << 24), (0x108, 1, 6)]


def _small_chain():
    from helpers import image
    from mcuaccel_backend.layers import Layer
    import numpy as np
    from array import array
    r = np.random.default_rng(5)
    H = W = 4
    C, O = 3, 16
    L = Layer("conv", H, W, C, O, 3, 3, 1, 1, 1, 1, -7, -128, -128, 127,
              array("b", r.integers(-128, 128, O * 9 * C).astype(np.int8).tobytes()),
              [int(b) for b in r.integers(-5000, 5000, O)], [int(m) for m in r.integers(1 << 30, 1 << 31, O)],
              [int(s) for s in r.integers(-10, 0, O)])
    x = r.integers(-128, 128, H * W * C).astype(np.int8).tobytes()
    mem, y_addr, ny, d_addr, cmds = image(L, 8, 1, 2, x)
    return mem, d_addr


def _flat(g):
    out = []
    for c in g:
        out += [("rd", a, t, 0) for a, t in c["desc"]]
        out += [("rd", a, t, 0) for a, t in c["wbuf"]]
        out += [("rd", a, t, 0) for a, t in c["pbuf"]]
        out += [("rd", a, t, 0) for a, t, _ in c["x"]]
        out.append(("st", c["stamp_addr"], 0, 0))
    return out


def test_ctrl_golden_and_checker():
    mem, d_addr = _small_chain()
    g = M.ctrl_golden(mem, d_addr, 8)
    assert len(g) == 1
    c = g[0]
    assert len(c["desc"]) == 17 and len(c["wbuf"]) == 18 * 8 and len(c["pbuf"]) == 48
    assert len(c["pushes"]) == 16 * 2
    assert sum(1 for a, t, m in c["x"] if t >> 2 & 1) > 0 and any(not (t >> 2 & 1) for a, t, m in c["x"])
    flat = _flat(g)
    assert M.check_requests(g, flat) == len(flat)
    # shuffled DESC / WBUF / PBUF groups are fine; a changed X tag, address or a swap is not
    head = flat[:17]
    random.Random(1).shuffle(head)
    sh = head + flat[17:]
    assert M.check_requests(g, sh) == len(flat)
    bad = list(flat)
    i = 17 + 144 + 48 + 5
    bad[i] = bad[i][:2] + (bad[i][2] ^ (1 << 11), 0)
    with pytest.raises(AssertionError):
        M.check_requests(g, bad)
    bad = list(flat)
    bad[i], bad[i + 1] = bad[i + 1], bad[i]
    with pytest.raises(AssertionError):
        M.check_requests(g, bad)
    with pytest.raises(AssertionError):
        M.check_requests(g, flat[:-1])
    assert M.check_requests(g, flat[:100], partial=True) == 100
    assert M.check_requests(g, flat[:-1], partial=True) == len(flat) - 1
