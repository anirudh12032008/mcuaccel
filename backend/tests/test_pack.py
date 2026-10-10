"""DESIGN §6.4 worked example: cifar op_0 (3x3, C=3, O=16, P=8, pad 1)."""
import random
import struct
from array import array

from mcuaccel_backend.desc import build_descriptors
from mcuaccel_backend.layers import Layer
from mcuaccel_backend.pack import pack_params, pack_weights


def op0(H=32, W=32):
    r = random.Random(7)
    w = array("b", [r.randint(-128, 127) for _ in range(16 * 27)])
    return Layer("conv", H, W, 3, 16, 3, 3, 1, 1, 1, 1, -7, -128, -128, 127, w,
                 [r.randint(-9999, 9999) for _ in range(16)], [r.randint(1 << 30, (1 << 31) - 1) for _ in range(16)],
                 [r.randint(-9, 0) for _ in range(16)])


def W(L, o, ky, kx, c):
    return L.weights[((o * 3 + ky) * 3 + kx) * 3 + c] & 255


def test_weight_layout():
    L = op0()
    wb, rows, rpt = pack_weights(L, 8, 0, 16)
    assert (len(wb), rows, rpt) == (576, 18, 9)
    assert wb[517] == W(L, 9, 2, 1, 2)
    lanes = {0: [(0, 0), (0, 1), (0, 2), (1, 0)], 1: [(1, 1), (1, 2), (2, 0), (2, 1)], 2: [(2, 2), None, None, None]}
    for t in range(2):
        for p in range(8):
            for ky in range(3):
                for j in range(3):
                    for l, kc in enumerate(lanes[j]):
                        want = 0 if kc is None else W(L, t * 8 + p, ky, *kc)
                        assert wb[4 * (((t * 3 + ky) * 3 + j) * 8 + p) + l] == want


def test_partial_tile_and_params():
    L = op0()
    wb, rows, _ = pack_weights(L, 8, 8, 5)         # one tile, 5 of 8 channels
    assert rows == 9 and len(wb) == 9 * 32
    assert all(wb[4 * (r * 8 + p) + l] == 0 for r in range(9) for p in range(5, 8) for l in range(4))
    pb = pack_params(L, 0, 16)
    assert struct.unpack_from("<3i", pb, 108) == (L.bias[9], L.mult[9], L.shift[9])


def test_worked_descriptor():
    L = op0()
    inp = 0x1000
    (d,) = build_descriptors(L, 8, inp, 0x8000, 0x4000, 0x6000, 0)
    assert len(d) == 18
    assert d[1] == 0x01111330 and d[2] == d[3] == 0x00200020 and d[4] == 0x00100003
    assert d[5] == 0x00030009 and d[6] == inp - 99 and d[7] == 3 and d[8] == 96
    assert d[9] == 3 and d[10] == 96 and d[12] == 16 and d[14] == 0x00090012
    assert d[16] == 0x7F8080F9 and d[0] == 0 and d[17] == 0 and d[11] == 0x8000
