"""Pure-Python executor of descriptor semantics (DESIGN §6.2-6.4, §7.1). The RTL is built to match.

`run_descriptor(mem, desc_addr, P, stats=False, trace=None)` runs the chain starting at
`desc_addr` in bytearray `mem` (addresses = indices) until NEXT == 0. Memory is little endian.

De-facto semantics fixed here (and to be copied into DESIGN):
- Only words 0..16 are read; STAMP (word 17) is NOT written (cycle counts belong to the M1 model
  / RTL; `stats` gives the counts that determine them).
- Params: bias/mult are the int32 words; shift = sign-extended bits [5:0] of its word.
- Accumulation is exact modulo 2^32 (wrapped once per tile; identical to wrapping per add).
- Per output: s = wrap32(acc + bias); r = int8ops.requant(s, mult, shift); v = r + y_zp (unbounded);
  y = clamp(v, act_min, act_max) in that order (lo test first); byte = y & 255. act_* are int8.
- Pixel order oy-major, then tile t, then ky (rows ky0..ky1), then quads j0..j1 (conv) / kx (dw).
  A tile is NV = min(P, NCH - t*P) outputs (conv) or 4 (dw), written with byte writes at
  yaddr = Y_BASE + (oy*OW+ox)*YPIX + ch0 (ch0 = t*P conv, 4t dw); only those NV bytes change.
- Conv x reads: s = rowA + 4*j0, a = s mod 4; a != 0 -> one prime read of the word at s - a
  (mac_en = 0), then one read per j at the following aligned addresses; quad = bytes a.. of the
  previous+current word (aligner); lane l is masked (x - x_zp := 0) unless b0 <= 4j+l < b1.
  Words outside the tensor are read (up to 6 bytes) but masked; an address outside `mem` raises
  IndexError (the RTL: bus err).
- Dw: per (ky, kx) one aligned word read at rowA + kx*XPIX + 4t, all lanes enabled, lane l -> acc l.
- Border: ky0 = max(0,-iy0), ky1 = min(KH, H-iy0), kx0/kx1 likewise; b0 = kx0*C, b1 = kx1*C.

stats=True returns one dict per command: x_words (R total, incl. primes), prime_words, mac_words,
w_words (weight words loaded), p_words (3*NCH), d_words (17), y_words (sum of W_t), tiles, pixels,
out_bytes. `trace` (a list) receives ("X", addr, mac_en, a, mask, first, last, wrow) per x read and
("T", ch0, nv, yaddr) at each tile's last read.
"""
from __future__ import annotations

import struct

from mcugrad.quant.int8ops import _clamp, _wrap32, requant

SB = tuple(b if b < 128 else b - 256 for b in range(256))
M32 = 0xFFFFFFFF


def _rd(mem, addr):
    addr &= M32
    if addr + 4 > len(mem):
        raise IndexError(f"bus error at {addr:#x}")
    return mem[addr:addr + 4]


def run_descriptor(mem, desc_addr, P, stats=False, trace=None):
    out = []
    while desc_addr:
        out.append(_run_one(mem, desc_addr, P, trace))
        desc_addr = struct.unpack_from("<I", mem, desc_addr)[0]
    return out if stats else None


def _run_one(mem, daddr, P, trace):
    d = struct.unpack_from("<17I", mem, daddr)
    dw = d[1] & 1
    KH, KW, SH, SW, PH, PW = (d[1] >> s & 15 for s in (4, 8, 12, 16, 20, 24))
    H, W, OH, OW = d[2] & 0xFFFF, d[2] >> 16, d[3] & 0xFFFF, d[3] >> 16
    NCH = d[4] >> 16
    C = d[4] & 0xFFFF
    run, rq = d[5] & 0xFFFF, d[5] >> 16 & 0x3FF
    xo, xpix, xrow, xsw, xsh, ybase, ypix = d[6], d[7], d[8], d[9], d[10], d[11], d[12]
    w_rows, rpt = d[14] & 0x3FF, d[14] >> 16 & 0x3FF
    x_zp, y_zp, amin, amax = (SB[d[16] >> s & 255] for s in (0, 8, 16, 24))
    st = dict(x_words=0, prime_words=0, mac_words=0, w_words=0, p_words=0, d_words=17, y_words=0,
              tiles=0, pixels=0, out_bytes=0)

    # WLOAD: wbuf[row][p] = 4 signed weights
    wpw = 1 if dw else P
    wbuf = []
    for r in range(w_rows):
        base = d[13] + 4 * r * wpw
        wbuf.append([tuple(SB[b] for b in _rd(mem, base + 4 * p)) for p in range(wpw)])
    st["w_words"] = w_rows * wpw
    # PLOAD
    pbuf = []
    for k in range(NCH):
        b = d[15] + 12 * k
        bias, mult = struct.unpack("<ii", bytes(_rd(mem, b)) + bytes(_rd(mem, b + 4)))
        sh = struct.unpack("<I", _rd(mem, b + 8))[0] & 63
        pbuf.append((bias, mult, sh - 64 if sh > 31 else sh))
    st["p_words"] = 3 * NCH

    nt = NCH // 4 if dw else (NCH + P - 1) // P
    nacc = 4 if dw else P
    for oy in range(OH):
        for ox in range(OW):
            st["pixels"] += 1
            iy0, ix0 = oy * SH - PH, ox * SW - PW
            ky0, ky1 = max(0, -iy0), min(KH, H - iy0)
            kx0, kx1 = max(0, -ix0), min(KW, W - ix0)
            b0, b1 = kx0 * C, kx1 * C
            j0, j1 = b0 >> 2, (b1 + 3) >> 2
            row0 = (xo + oy * xsh + ox * xsw + ky0 * xrow) & M32
            ypixa = ybase + (oy * OW + ox) * ypix
            for t in range(nt):
                acc = [0] * nacc
                reads = []                       # (mac_en, a, mask, wrow, quad bytes)
                rowa = row0
                wr = t * rpt + ky0 * (KW if dw else rq)
                for ky in range(ky0, ky1):
                    if dw:
                        for kx in range(kx0, kx1):
                            ad = rowa + kx * xpix + 4 * t
                            reads.append((ad, 1, 0, 15, wr + kx, bytes(_rd(mem, ad))))
                        wr += KW
                    else:
                        s = rowa + 4 * j0
                        a = s & 3
                        ad = s - a
                        prev = None
                        if a:
                            prev = bytes(_rd(mem, ad))
                            reads.append((ad, 0, a, 0, 0, prev))
                            ad += 4
                        for j in range(j0, j1):
                            cur = bytes(_rd(mem, ad))
                            quad = cur if not a else (prev + cur)[a:a + 4]
                            mask = sum(1 << l for l in range(4) if b0 <= 4 * j + l < b1)
                            reads.append((ad, 1, a, mask, wr + j, quad))
                            prev = cur
                            ad += 4
                        wr += rq
                    rowa += xrow
                macs = [r for r in reads if r[1]]
                for n, (ad, en, a, mask, wrow, quad) in enumerate(reads):
                    if trace is not None:
                        lastq = macs and reads[n] is macs[-1]
                        trace.append(("X", ad & M32, en, a, mask, int(en and reads[n] is macs[0]), int(bool(lastq)), wrow))
                    if not en:
                        continue
                    xs = [SB[quad[l]] - x_zp if mask >> l & 1 else 0 for l in range(4)]
                    row = wbuf[wrow]
                    if dw:
                        w = row[0]
                        for l in range(4):
                            acc[l] += xs[l] * w[l]
                    else:
                        for p in range(P):
                            w = row[p]
                            acc[p] += xs[0] * w[0] + xs[1] * w[1] + xs[2] * w[2] + xs[3] * w[3]
                nv = 4 if dw else min(P, NCH - t * P)
                ch0 = 4 * t if dw else t * P
                ya = ypixa + ch0
                if trace is not None:
                    trace.append(("T", ch0, nv, ya & M32))
                st["x_words"] += len(reads)
                st["prime_words"] += len(reads) - len(macs)
                st["mac_words"] += len(macs)
                st["tiles"] += 1
                st["y_words"] += ((ya & 3) + nv + 3) >> 2
                st["out_bytes"] += nv
                for i in range(nv):
                    bias, mult, sh = pbuf[ch0 + i]
                    r = requant(_wrap32(acc[i] + bias), mult, sh)
                    v = _clamp(r + y_zp, amin, amax)
                    if not 0 <= ya + i < len(mem):
                        raise IndexError(f"bus error at {ya + i:#x}")
                    mem[ya + i] = v & 255
    return st
