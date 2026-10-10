"""One accelerated op in the accelerator's terms, and the SPEC §3 / DESIGN §6.4 support check.

Conventions (de-facto semantics, mirrored by the RTL):
- fc is a 1x1 conv: H=1, W=N, C=K, O=M, KH=KW=SH=SW=1, PH=PW=0, mode "fc".
- dw layers have C == O == groups and weights [O][KH][KW][1] (OHWI with Cg = 1).
- `supported()` returns True or a reason string (a str is truthy: test with `is True`).
  It cannot see addresses: for dw the caller must still place the input tensor 4-byte aligned
  (split.build_commands asserts it).
"""
from __future__ import annotations

from array import array
from dataclasses import dataclass

INT32_MAX = (1 << 31) - 1
ROWS = 512      # weight buffer rows
MAXCH = 512     # parameter buffer channels
DIM = 65535


@dataclass
class Layer:
    mode: str                    # "conv" | "dw" | "fc"
    H: int
    W: int
    C: int
    O: int
    KH: int
    KW: int
    SH: int
    SW: int
    PH: int
    PW: int
    x_zp: int
    y_zp: int
    act_min: int
    act_max: int
    weights: array               # int8, OHWI [O][KH][KW][Cg]
    bias: list
    mult: list
    shift: list

    @property
    def OH(self):
        return (self.H + 2 * self.PH - self.KH) // self.SH + 1

    @property
    def OW(self):
        return (self.W + 2 * self.PW - self.KW) // self.SW + 1


def rows_per_tile(layer):
    """Weight-buffer rows one tile needs (KH*RQ for conv/fc, KH*KW for dw)."""
    if layer.mode == "dw":
        return layer.KH * layer.KW
    return layer.KH * ((layer.KW * layer.C + 3) // 4)


def check(layer):
    """True or the reason the layer cannot run on the accelerator (SPEC §3, §4; DESIGN §6.4)."""
    L = layer
    if L.mode not in ("conv", "dw", "fc"):
        return f"mode {L.mode}"
    if not (1 <= L.KH <= 15 and 1 <= L.KW <= 15 and 1 <= L.SH <= 15 and 1 <= L.SW <= 15):
        return "kernel/stride outside 1..15"
    if not (L.PH < L.KH and L.PW < L.KW):
        return "pad >= kernel"
    if L.PH < 0 or L.PW < 0:
        return "negative pad"
    if min(L.H, L.W, L.C, L.O) < 1 or max(L.H, L.W, L.C, L.O) > DIM:
        return "H/W/C/O outside 1..65535"
    if L.OH < 1 or L.OW < 1 or L.OH > DIM or L.OW > DIM:
        return "OH/OW outside 1..65535"
    if L.mode == "dw":
        if L.C != L.O or L.C % 4:
            return "dw needs C == O, C % 4 == 0"
        K = L.KH * L.KW
    else:
        if L.mode == "fc" and not (L.H == 1 and L.KH == L.KW == L.SH == L.SW == 1 and L.PH == L.PW == 0):
            return "fc must be 1x1 with H=1"
        if L.KW * L.C > DIM:
            return "RUN_BYTES KW*C > 65535"
        K = L.KH * L.KW * L.C
    if rows_per_tile(L) > ROWS:
        return f"rows per tile {rows_per_tile(L)} > {ROWS}"
    for v in (L.x_zp, L.y_zp, L.act_min, L.act_max):
        if not -128 <= v <= 127:
            return "zero point / clamp not int8"
    if not (len(L.bias) == len(L.mult) == len(L.shift) == L.O):
        return "bias/mult/shift length != O"
    if len(L.weights) != L.O * L.KH * L.KW * (1 if L.mode == "dw" else L.C):
        return "weights size"
    if any(not -31 <= s <= 31 for s in L.shift):
        return "shift outside [-31, 31]"
    if any(not -(1 << 31) <= m <= INT32_MAX for m in L.mult):
        return "mult not int32"
    if max(abs(b) for b in L.bias) + K * 255 * 128 > INT32_MAX:
        return "accumulator bound |bias| + K*255*128 > 2^31-1"
    return True


def from_node(graph, node):
    """Layer for an int8-graph conv2d(_fused) / matmul(_fused) node (N must be 1)."""
    a = node.attrs
    xs = graph.values[node.inputs[0]].shape
    wc = graph.consts[node.inputs[1]]
    common = dict(x_zp=a["x_zp"], y_zp=a["y_zp"], act_min=a["act_min"], act_max=a["act_max"],
                  bias=list(graph.consts[node.inputs[2]].data), mult=list(a["out_mult"]),
                  shift=list(a["out_shift"]))
    if xs[0] != 1:
        raise ValueError("batch N != 1")
    if node.op in ("conv2d", "conv2d_fused"):
        _, H, W, C = xs
        O, KH, KW, Cg = wc.shape
        SH, SW = a.get("stride", (1, 1))
        PH, PW = a.get("pad", (0, 0))
        g = a.get("groups", 1)
        if g == 1:
            mode = "conv"
        elif g == C == O:
            mode = "dw"
        else:
            raise ValueError(f"grouped conv groups={g} C={C} O={O}")
        return Layer(mode, H, W, C, O, KH, KW, SH, SW, PH, PW, weights=wc.data, **common)
    if node.op in ("matmul", "matmul_fused"):
        N, K = xs
        M = wc.shape[1]
        w = array("b", [wc.data[k * M + m] for m in range(M) for k in range(K)])  # [K,M] -> [M,K]
        return Layer("fc", 1, N, K, M, 1, 1, 1, 1, 0, 0, weights=w, **common)
    raise ValueError(f"op {node.op}")


def supported(graph, node):
    """True, or a reason string (also for ops the accelerator never takes)."""
    try:
        return check(from_node(graph, node))
    except ValueError as e:
        return str(e)
