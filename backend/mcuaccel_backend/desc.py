"""DESIGN §6.2 descriptor words (18 per descriptor, STAMP word emitted as 0).

Resolutions: X_ORIGIN = x_base - PH*XROW - PW*XPIX (+ c0 for a dw split), mod 2^32; XPIX = layer C
(conv/fc: = C of the command; dw: stride of the whole layer); Y_BASE = y_base + o_s; YPIX = layer O;
CH = C | NCH << 16 where for dw C = NCH = channels of this command.
"""
from __future__ import annotations

M32 = 0xFFFFFFFF


def make_descriptor(layer, o_s, nch, x_addr, y_addr, w_addr, p_addr, next_addr, w_rows, rpt):
    L = layer
    dw = L.mode == "dw"
    xpix = L.C
    xrow = L.W * xpix
    run = 0 if dw else L.KW * L.C
    rq = 0 if dw else (run + 3) // 4
    w = [0] * 18
    w[0] = next_addr & M32
    w[1] = (dw | L.KH << 4 | L.KW << 8 | L.SH << 12 | L.SW << 16 | L.PH << 20 | L.PW << 24)
    w[2] = L.H | L.W << 16
    w[3] = L.OH | L.OW << 16
    w[4] = (nch if dw else L.C) | nch << 16
    w[5] = run | rq << 16
    w[6] = (x_addr - L.PH * xrow - L.PW * xpix + (o_s if dw else 0)) & M32
    w[7] = xpix
    w[8] = xrow
    w[9] = L.SW * xpix
    w[10] = L.SH * xrow
    w[11] = (y_addr + o_s) & M32
    w[12] = L.O
    w[13] = w_addr & M32
    w[14] = w_rows | rpt << 16
    w[15] = p_addr & M32
    w[16] = ((L.x_zp & 255) | (L.y_zp & 255) << 8 | (L.act_min & 255) << 16 | (L.act_max & 255) << 24)
    return w


def build_descriptors(layer, P, x_addr, y_addr, w_addr, p_addr, next_addr, desc_addr=0):
    """Descriptors of all splits of `layer`. W/P regions of split k follow those of split k-1
    (split.build_commands); NEXT of split k < last is desc_addr + 72*(k+1) (descriptors are
    contiguous from desc_addr, required when there is more than one split), the last gets next_addr."""
    from .split import build_commands
    return [c.desc for c in build_commands(layer, P, x_addr, y_addr, w_addr, p_addr, next_addr, desc_addr)]
