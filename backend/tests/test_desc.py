"""Descriptor chains, splits, executor statistics (independent recomputation of DESIGN §7.1 R(pix))."""
import struct

import pytest
from helpers import gen_layer, image
from mcuaccel_backend.desc import build_descriptors
from mcuaccel_backend.exec import run_descriptor
from mcuaccel_backend.split import split_plan
from test_exec_vs_int8ops import _fixed
from test_pack import op0


def expected(L, P, x_addr, y_addr):
    """(x_words, y_words, tiles) summed over the whole layer, straight from §7.1."""
    nt = L.C // 4 if L.mode == "dw" else None
    R = Y = T = 0
    xrow = L.W * L.C
    for oy in range(L.OH):
        for ox in range(L.OW):
            iy0, ix0 = oy * L.SH - L.PH, ox * L.SW - L.PW
            ky = range(max(0, -iy0), min(L.KH, L.H - iy0))
            kx0, kx1 = max(0, -ix0), min(L.KW, L.W - ix0)
            if L.mode == "dw":
                tiles = [(4 * t, 4, len(ky) * (kx1 - kx0)) for t in range(nt)]
            else:
                b0, b1 = kx0 * L.C, kx1 * L.C
                j0, j1 = b0 >> 2, (b1 + 3) >> 2
                r = sum((j1 - j0) + ((x_addr + (iy0 + k) * xrow + ix0 * L.C + 4 * j0) % 4 != 0) for k in ky)
                tiles = [(t * P, min(P, L.O - t * P), r) for t in range((L.O + P - 1) // P)]
            for ch0, nv, r in tiles:
                ya = y_addr + (oy * L.OW + ox) * L.O + ch0
                R += r
                Y += ((ya % 4) + nv + 3) // 4
                T += 1
    return R, Y, T


@pytest.mark.parametrize("seed", range(60))
def test_stats_match_formula(seed):
    L, P, xa, ya, x = gen_layer(seed)
    mem, y_addr, ny, d_addr, cmds = image(L, P, xa, ya, x, seed)
    st = run_descriptor(mem, d_addr, P, stats=True)
    R, Y, T = expected(L, P, 16 + xa, y_addr)
    assert (sum(s["x_words"] for s in st), sum(s["y_words"] for s in st), sum(s["tiles"] for s in st)) == (R, Y, T)
    for s, c in zip(st, cmds):
        assert s["w_words"] == c.desc[14] % 1024 * (1 if L.mode == "dw" else P) and s["p_words"] == 3 * c.nch
        assert s["x_words"] == s["prime_words"] + s["mac_words"] and s["d_words"] == 17


def test_worked_example_first_pixel_trace():
    L = op0(4, 4)                                   # same border/alignment behaviour as 32x32
    x = bytes(range(48))
    mem, y_addr, ny, d_addr, _ = image(L, 8, 0, 0, x)
    tr = []
    run_descriptor(mem, d_addr, 8, trace=tr)
    # pixel (0,0), tile 0: ky=1 row starts at input-3: a=1 -> 1 prime, 3 quads, lanes 0..2 of quad 0 masked
    assert tr[0] == ("X", 12, 0, 1, 0, 0, 0, 0)
    assert tr[1] == ("X", 16, 1, 1, 0b1000, 1, 0, 3)
    assert tr[2][3:5] == (1, 0b1111) and tr[3][3:5] == (1, 0b0001)
    t = next(e for e in tr if e[0] == "T")
    assert t[1:3] == (0, 8) and tr[tr.index(t) - 1][6] == 1


def test_chain_and_split_layout():
    L, x = _fixed("conv", 5, 5, 20, 40, 5, 4, 3)
    ds = build_descriptors(L, 4, 100, 2000, 3000, 5000, 0xABC0, desc_addr=7000)
    plan = split_plan(L, 4)
    assert plan == [(0, 16), (16, 16), (32, 8)] and len(ds) == 3
    assert [d[0] for d in ds] == [7072, 7144, 0xABC0]
    assert [d[11] for d in ds] == [2000, 2016, 2032] and [d[4] >> 16 for d in ds] == [16, 16, 8]
    assert ds[1][13] - ds[0][13] == 4 * 4 * 125 * 4 and ds[1][15] - ds[0][15] == 12 * 16
    with pytest.raises(AssertionError):
        build_descriptors(L, 4, 100, 2000, 3000, 5000, 0)       # split layer needs desc_addr


def test_executor_follows_chain_two_layers():
    # layer B reads layer A's output region: executor must follow NEXT in order
    from helpers import reference
    import numpy as np
    from mcuaccel_backend.split import build_commands, write_image
    LA, xa = _fixed("conv", 4, 4, 8, 8, 1, 8, 5)
    r = np.random.default_rng(1)
    LB, _ = _fixed("fc", 1, 1, 128, 6, 1, 8, 6)
    LB.H, LB.W, LB.C = 1, 1, 128
    mem = bytearray(r.integers(0, 256, 20000).astype(np.uint8).tobytes())
    xA, yA, yB = 1000, 2000, 3000
    mem[xA:xA + len(xa)] = xa
    ca = build_commands(LA, 8, xA, yA, 4000, 5000, 8000, 7000)
    LB.mode = "fc"
    cb = build_commands(LB, 8, yA, yB, 9000, 10000, 0, 8000)
    write_image(mem, ca + cb)
    run_descriptor(mem, 7000, 8)
    ya = reference(LA, xa)
    assert bytes(mem[yA:yA + 128]) == ya.tobytes()
    assert bytes(mem[yB:yB + 6]) == reference(LB, bytes(mem[yA:yA + 128])).tobytes()
