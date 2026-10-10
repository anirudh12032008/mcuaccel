"""DESIGN §6.4 packing. Weights are packed per command (output channels o_s .. o_s+nch-1).

Conv/fc: row r = (t*KH + ky)*RQ + j holds P words; word p byte l = w[o_s+t*P+p][ky][b div C][b mod C]
with b = 4j+l, 0 when b >= KW*C or t*P+p >= nch.  Dw: one word per row r = g*KH*KW + ky*KW + kx,
byte l = w[o_s+4g+l][ky][kx][0]. Parameter record k (3 little-endian int32 words) = bias, mult, shift.
"""
from __future__ import annotations

import struct


def pack_weights(layer, P, o_s, nch):
    """-> (bytes, w_rows, rows_per_tile)."""
    L, w = layer, layer.weights
    if L.mode == "dw":
        assert nch % 4 == 0
        kk = L.KH * L.KW
        out = bytearray(4 * (nch // 4) * kk)
        for i in range(nch):
            g, l = divmod(i, 4)
            for k in range(kk):                      # k = ky*KW + kx; Cg = 1
                out[4 * (g * kk + k) + l] = w[(o_s + i) * kk + k] & 255
        return bytes(out), (nch // 4) * kk, kk
    run = L.KW * L.C
    rq = (run + 3) // 4
    nt = (nch + P - 1) // P
    out = bytearray(4 * P * nt * L.KH * rq)
    for i in range(nch):
        t, p = divmod(i, P)
        for ky in range(L.KH):
            base = (o_s + i) * L.KH * run + ky * run
            for b in range(run):
                j, l = divmod(b, 4)
                out[4 * (((t * L.KH + ky) * rq + j) * P + p) + l] = w[base + b] & 255
    return bytes(out), nt * L.KH * rq, L.KH * rq


def pack_params(layer, o_s, nch):
    v = []
    for o in range(o_s, o_s + nch):
        v += [layer.bias[o], layer.mult[o], layer.shift[o]]
    return struct.pack(f"<{len(v)}i", *v)
