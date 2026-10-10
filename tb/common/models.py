"""Pure-Python reference models for the M2 unit tests (DESIGN section 8; no cocotb import).

Each model is the cycle-free function of one module:
  align    quad formation from {cur, prev} (8.8)
  mac      S1/S2 arithmetic of mcuaccel_mac_array incl. masks, dw, first/last, wrap mod 2^32 (8.9)
  requant  bias add + int8ops.requant + y_zp + signed clamp (8.10 / SPEC 4) and an independent
           bit-level formulation of the S1..S5 pipeline stages (checked equal in test_models_selfcheck)
  writer   byte-to-word assembly (8.11)
  ctrl     golden request stream of mcuaccel_ctrl derived from the Python executor trace (8.2, 8.5, 7.1)
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "third_party/mcugrad", ROOT / "backend"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from mcugrad.quant.int8ops import _clamp, _wrap32, requant  # noqa: E402

M32 = 0xFFFF_FFFF
INT32_MIN, INT32_MAX = -(1 << 31), (1 << 31) - 1


def s8(v):
    v &= 0xFF
    return v - 256 if v & 0x80 else v


def s32(v):
    return _wrap32(v)


def u32(v):
    return v & M32


def nacc(P):
    return max(P, 4)


# ------------------------------------------------------------------------------------ align
def align_quad(prev, cur, a):
    """DESIGN 8.8: a == 0 -> cur, else low 32 bits of {cur, prev} >> (8a)."""
    cur, prev = cur & M32, prev & M32
    if a == 0:
        return cur
    return ((cur << 32 | prev) >> (8 * a)) & M32


class AlignModel:
    """`prev` loads on every valid input word; quad is a function of (prev, in_word, in_a)."""

    def __init__(self):
        self.prev = 0

    def quad(self, word, a):
        return align_quad(self.prev, word, a)

    def clock(self, valid, word):
        if valid:
            self.prev = word & M32


# -------------------------------------------------------------------------------------- mac
def mac_beat(quad, mask, x_zp, in_w, dw, P):
    """Per-accumulator increment of one beat. in_w: list of P unsigned 32-bit words.
    conv: inc[p] = sum_l xs[l] * w[p][l] (p < P);  dw: inc[l] = xs[l] * w[0][l] (l < 4).
    xs[l] = mask[l] ? s8(x_l) - s8(x_zp) : 0."""
    xs = [(s8(quad >> (8 * l)) - s8(x_zp)) if (mask >> l) & 1 else 0 for l in range(4)]
    if dw:
        return [xs[l] * s8(in_w[0] >> (8 * l)) for l in range(4)]
    return [sum(xs[l] * s8(in_w[p] >> (8 * l)) for l in range(4)) for p in range(P)]


class MacModel:
    """Accumulators wrap mod 2^32 (exact, SPEC 4 rule 3). beat() returns the signed accumulator
    list (valid entries: dw -> 0..3, conv -> 0..P-1) when `last`, else None."""

    def __init__(self, P):
        self.P = P
        self.acc = [0] * nacc(P)

    def beat(self, quad, mask, x_zp, in_w, dw, first, last):
        inc = mac_beat(quad, mask, x_zp, in_w, dw, self.P)
        for i, v in enumerate(inc):
            self.acc[i] = s32((0 if first else self.acc[i]) + v)
        return list(self.acc[:len(inc)]) if last else None


# ---------------------------------------------------------------------------------- requant
def sext6(v):
    v &= 63
    return v - 64 if v > 31 else v


def requant_out(acc, bias, mult, shift6, y_zp, act_min, act_max):
    """Output byte: clamp(requant(wrap32(acc + bias), mult, shift) + y_zp, act_min, act_max) & 255.
    acc, bias, mult, y_zp, act_* signed ints; shift6 = the 6-bit pbuf shift field."""
    s = _wrap32(acc + bias)
    r = requant(s, mult, sext6(shift6))
    return _clamp(r + y_zp, act_min, act_max) & 0xFF


def requant_pipeline(acc, bias, mult, shift6, y_zp, act_min, act_max):
    """Independent formulation, stage by stage as DESIGN 8.10 S1..S5 (closed forms, no int8ops)."""
    s = _wrap32(acc + bias)                                   # S1
    sh = sext6(shift6)
    ls = sh if sh > 0 else 0
    rs = 0 if sh > 0 else -sh
    av = _wrap32(s << ls)
    prod = av * mult                                          # S2 (64 b)
    if av == INT32_MIN and mult == INT32_MIN:                 # S3
        x = INT32_MAX
    else:
        x = _wrap32(((prod + (1 << 30)) >> 31) & M32)         # bits [62:31]
    mask = (1 << rs) - 1                                      # S4
    r = (x >> rs) + (1 if (x & mask) > ((mask >> 1) + (1 if x < 0 else 0)) else 0)
    v = r + y_zp                                              # S5 (33 b)
    y = act_min if v < act_min else (act_max if v > act_max else v)
    return y & 0xFF


# ----------------------------------------------------------------------------------- writer
def writer_words(stream):
    """stream: list of (addr, byte, last) in input order -> [(word_addr, be, data)].
    Lane = addr & 3; a word is emitted when a byte has lane 3 or `last`. Data lanes with be = 0
    are 0 here (the RTL value there is don't care: compare through be)."""
    out, be, data, waddr = [], 0, 0, None
    for addr, b, last in stream:
        lane = addr & 3
        waddr = addr & ~3
        be |= 1 << lane
        data |= (b & 0xFF) << (8 * lane)
        if lane == 3 or last:
            out.append((waddr, be, data))
            be, data = 0, 0
    assert be == 0, "stream ends inside a word without in_last"
    return out


def mask_data(data, be):
    return data & sum(0xFF << (8 * l) for l in range(4) if be >> l & 1)


# ------------------------------------------------------------------------------------ ctrl
SB = tuple(b if b < 128 else b - 256 for b in range(256))
DEST_DESC, DEST_WBUF, DEST_PBUF, DEST_X = 0, 1, 2, 3


def tag_desc(i):
    return DEST_DESC | i << 2


def tag_wbuf(row, word):
    return DEST_WBUF | row << 2 | word << 11


def tag_pbuf(ch, sel):
    return DEST_PBUF | ch << 2 | sel << 11


def tag_x(mac_en, mask, a, first, last, wrow):
    return DEST_X | mac_en << 2 | mask << 3 | a << 7 | first << 9 | last << 10 | wrow << 11


def decode_desc(mem, d_addr):
    d = struct.unpack_from("<17I", mem, d_addr)
    return dict(next=d[0], dw=d[1] & 1, nch=d[4] >> 16, w_base=d[13], w_rows=d[14] & 0x3FF,
                p_base=d[15], x_zp=SB[d[16] & 255], y_zp=SB[d[16] >> 8 & 255],
                act_min=SB[d[16] >> 16 & 255], act_max=SB[d[16] >> 24 & 255])


X_PRIME_CMP = 0x3 | 1 << 2 | 3 << 7      # dest, mac_en, a (prime reads: other fields don't care)
FULL_CMP = 0xF_FFFF


def ctrl_golden(mem, d_addr, P):
    """Golden request stream for the chain at d_addr, one dict per command:
      desc/wbuf/pbuf : [(addr, tag)]  (any order within the group is accepted)
      x              : [(addr, tag, tag_cmp_mask)]  (exact order)
      pushes         : [(ch0, nv, yaddr, x_index_of_last_request)]
      stamp_addr, dd (decoded descriptor)
    from the executor's trace (backend/mcuaccel_backend/exec.py)."""
    from mcuaccel_backend.exec import _run_one
    mem = bytearray(mem)
    cmds = []
    while d_addr:
        dd = decode_desc(mem, d_addr)
        tr = []
        _run_one(mem, d_addr, P, tr)
        xs, pushes = [], []
        for e in tr:
            if e[0] == "X":
                _, addr, en, a, mask, first, last, wrow = e
                if en:
                    xs.append((addr, tag_x(1, mask, a, first, last, wrow), FULL_CMP))
                else:
                    xs.append((addr, tag_x(0, 0, a, 0, 0, 0), X_PRIME_CMP))
            else:
                _, ch0, nv, ya = e
                pushes.append((ch0, nv, ya, len(xs) - 1))
        wpw = 1 if dd["dw"] else P
        cmds.append(dict(
            d_addr=d_addr, dd=dd, stamp_addr=d_addr + 0x44,
            desc=[(d_addr + 4 * i, tag_desc(i)) for i in range(17)],
            wbuf=[(dd["w_base"] + 4 * k, tag_wbuf(k // wpw, k % wpw)) for k in range(dd["w_rows"] * wpw)],
            pbuf=[(dd["p_base"] + 4 * k, tag_pbuf(k // 3, k % 3)) for k in range(3 * dd["nch"])],
            x=xs, pushes=pushes))
        d_addr = dd["next"]
    return cmds


def check_requests(golden, reqs, partial=False):
    """reqs: accepted requests in acceptance order, tuples (kind, addr, tag, data) with kind
    'rd' | 'st'. Per command: 17 DESC reads, then the WBUF reads, then the PBUF reads (each group
    in any order but each element exactly once), then the X reads in the golden order, then one
    STAMP write at desc + 0x44. partial=True accepts a truncated sequence (abort) and returns
    the number of requests matched. Raises AssertionError with the first difference."""
    pos = 0
    for ci, c in enumerate(golden):
        for name in ("desc", "wbuf", "pbuf", "x"):
            exp = c[name]
            got = reqs[pos:pos + len(exp)]
            if len(got) < len(exp):
                assert partial, f"cmd {ci} {name}: only {len(got)} of {len(exp)} requests"
            for k, g in enumerate(got):
                assert g[0] == "rd", f"cmd {ci} {name}[{k}]: expected a read, got {g[0]} @{g[1]:#x}"
            if name == "x":
                for k, (g, e) in enumerate(zip(got, exp)):
                    cm = e[2]
                    assert g[1] == e[0] and (g[2] ^ e[1]) & cm == 0, (
                        f"cmd {ci} x[{k}]: got addr {g[1]:#x} tag {g[2]:#07x}, "
                        f"expected addr {e[0]:#x} tag {e[1]:#07x} (cmp {cm:#x})")
            else:
                want = dict(exp)
                seen = set()
                for k, g in enumerate(got):
                    assert g[1] in want and g[1] not in seen, (
                        f"cmd {ci} {name}: unexpected/duplicate read @{g[1]:#x}")
                    assert g[2] == want[g[1]], (
                        f"cmd {ci} {name} @{g[1]:#x}: tag {g[2]:#07x}, expected {want[g[1]]:#07x}")
                    seen.add(g[1])
            pos += len(got)
            if len(got) < len(exp):
                return pos
        if pos >= len(reqs):
            assert partial, f"cmd {ci}: no STAMP write"
            return pos
        r = reqs[pos]
        assert r[0] == "st" and r[1] == c["stamp_addr"], (
            f"cmd {ci}: expected STAMP write @{c['stamp_addr']:#x}, got {r[0]} @{r[1]:#x}")
        pos += 1
    assert pos == len(reqs), f"{len(reqs) - pos} extra requests after the chain"
    return pos
