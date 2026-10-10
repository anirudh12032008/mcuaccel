"""Output-channel (dw: channel) splitting of one layer into commands (DESIGN §6.4), each with its
own packed weights, parameter records and descriptor."""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .desc import make_descriptor
from .layers import MAXCH, ROWS, check, rows_per_tile
from .pack import pack_params, pack_weights


@dataclass
class Command:
    o_s: int
    nch: int
    w_addr: int
    w_bytes: bytes
    p_addr: int
    p_bytes: bytes
    d_addr: int
    desc: list


def split_plan(layer, P):
    """[(o_s, nch)]: conv/fc NCH = P*floor(512/rows_per_tile) (<= 512, multiple of P);
    dw NCH = 4*floor(512/(KH*KW)) (<= 512)."""
    rpt = rows_per_tile(layer)
    if layer.mode == "dw":
        step = min(4 * (ROWS // rpt), MAXCH)
    else:
        step = min(P * (ROWS // rpt), MAXCH // P * P)
    return [(o, min(step, layer.O - o)) for o in range(0, layer.O, step)]


def build_commands(layer, P, x_addr, y_addr, w_addr, p_addr, next_addr, desc_addr=0):
    r = check(layer)
    assert r is True, r
    assert layer.mode != "dw" or x_addr % 4 == 0, "dw input must be 4-byte aligned"
    plan = split_plan(layer, P)
    assert len(plan) == 1 or desc_addr, "desc_addr required for split layers"
    cmds = []
    for k, (o_s, nch) in enumerate(plan):
        wb, rows, rpt = pack_weights(layer, P, o_s, nch)
        pb = pack_params(layer, o_s, nch)
        nxt = desc_addr + 72 * (k + 1) if k + 1 < len(plan) else next_addr
        d = make_descriptor(layer, o_s, nch, x_addr, y_addr, w_addr, p_addr, nxt, rows, rpt)
        cmds.append(Command(o_s, nch, w_addr, wb, p_addr, pb, desc_addr + 72 * k, d))
        w_addr += len(wb)
        p_addr += len(pb)
    return cmds


def write_image(mem, cmds):
    """Copy weights, parameters and descriptors of `cmds` to their addresses in bytearray `mem`
    (addresses are indices into mem)."""
    for c in cmds:
        mem[c.w_addr:c.w_addr + len(c.w_bytes)] = c.w_bytes
        mem[c.p_addr:c.p_addr + len(c.p_bytes)] = c.p_bytes
        mem[c.d_addr:c.d_addr + 72] = struct.pack("<18I", *c.desc)
