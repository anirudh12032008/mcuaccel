"""Accelerated model.c emitter (DESIGN 6.5).

Reuses mcugrad.codegen.emit's own pieces (kernel text, per-node C, buffer plan) so fallback ops are
pasted byte-for-byte as mcugrad emits them; ops the backend supports (layers.supported) become
descriptor chains. Descriptors are built with all base addresses 0, so every address field in the
static initialiser is a pure offset; the first model_run adds the real bases (bind), and each call
re-adds only the base of `input` / `output` for the commands that read / write them.

    python -m mcuaccel_backend.emit --model mnist_cnn --P 8 --out DIR     (writes model.h, model.c)
"""
from __future__ import annotations

import argparse
import importlib
import os
import re
import struct

from mcugrad.ir.nodes import Graph

mg = importlib.import_module("mcugrad.codegen.emit")    # the package re-exports a function of the same name

from .layers import from_node, supported
from .split import build_commands

QUANT = "int8_pc"
FAKE_DESC = 0x1000    # build_commands wants a non-zero desc_addr for split layers; NEXT is rebuilt below


def _u(v: int) -> str:
    return f"0x{v & 0xFFFFFFFF:08x}u"


def _array(ctype: str, name: str, vals: list[str]) -> str:
    rows = [", ".join(vals[i:i + 8]) for i in range(0, len(vals), 8)]
    return f"static const {ctype} {name}[{len(vals)}] = {{\n    " + ",\n    ".join(rows) + "\n};\n"


def _accelerated(g: Graph, n, ptr: dict[str, str]) -> bool:
    """Backend support, plus the one address rule only the emitter can see: a dw layer needs a
    4-byte aligned x and `input` is caller-placed."""
    if supported(g, n) is not True:
        return False
    return not (from_node(g, n).mode == "dw" and ptr[n.inputs[0]] == "input")


def _arena(ptr: str) -> int | None:
    m = re.fullmatch(r"mg_arena \+ (\d+)", ptr)
    return int(m.group(1)) if m else None


def _nbytes(g: Graph, name: str) -> int:
    k = 1
    for d in g.values[name].shape:
        k *= d
    return k


def _check_addresses(g: Graph, n, ptr: dict[str, str]) -> None:
    """Emitter-only address rules (DESIGN 6.4): dw x is 4-byte aligned; x and y arena ranges are disjoint."""
    xo, yo = _arena(ptr[n.inputs[0]]), _arena(ptr[n.outputs[0]])
    if from_node(g, n).mode == "dw" and xo is not None:
        assert xo % 4 == 0, f"dw input of {n.outputs[0]} at mg_arena + {xo} is not 4-byte aligned"
    if xo is not None and yo is not None:
        assert (xo + _nbytes(g, n.inputs[0]) <= yo or yo + _nbytes(g, n.outputs[0]) <= xo), \
            f"x and y of {n.outputs[0]} overlap in the arena"


def plan(g: Graph):
    """-> (items, chains, ptr): items = [(node index, node, accelerated)] in execution order,
    chains = maximal runs of accelerated node indices (one descriptor chain each)."""
    _, _, ptr = mg.plan_graph(g)
    items: list[tuple[int, object, bool]] = []
    for i, n in enumerate(g.nodes):
        if n.op not in mg.ALIAS_OPS:
            items.append((i, n, _accelerated(g, n, ptr)))
    chains: list[list[int]] = []
    prev = False
    for i, n, acc in items:
        if acc:
            _check_addresses(g, n, ptr)
            if not prev:
                chains.append([])
            chains[-1].append(i)
        prev = acc
    return items, chains, ptr


def emit_model(g: Graph, P: int, model_name: str) -> dict[str, str]:
    """-> {"model.h", "model.c"} for int8 graph `g` accelerated with P MAC lanes."""
    files = mg.emit(g, model_name=model_name, quant_name=QUANT)    # model.h and the fallback reference
    _, p, _ = mg.plan_graph(g)
    hdr_src, kfuncs = mg._kernel_sources()
    items, chains, ptr = plan(g)

    consts, used, fb = [], [], {}                                  # fb: node index -> (call, node)
    for i, n, acc in items:
        if not acc:
            decls, call, k = mg._emit_node(g, i, n, ptr)
            if k not in used:
                used.append(k)
            if decls:
                consts.append(f"/* {n.op} -> {n.outputs[0]} */\n{decls}")
            fb[i] = call
    hdr_src = mg._used_helpers_only(hdr_src, "".join(kfuncs[k] for k in used))

    bind, bodies, ad_decl, funcs = [], {}, [], {}
    for c, members in enumerate(chains):
        descs = []                                                 # (words, x_ptr, y_ptr, node idx)
        for i in members:
            n = g.nodes[i]
            L = from_node(g, n)
            cmds = build_commands(L, P, 0, 0, 0, 0, 0, FAKE_DESC)
            wb = b"".join(cm.w_bytes for cm in cmds)
            pb = b"".join(cm.p_bytes for cm in cmds)
            consts.append(f"/* accelerated {n.op} -> {n.outputs[0]} (mode {L.mode}, {len(cmds)} cmd) */\n"
                          + _array("uint32_t", f"aw_{i}", [_u(v) for v in struct.unpack(f"<{len(wb) // 4}I", wb)])
                          + _array("int32_t", f"ap_{i}", [mg._c_int(v) for v in struct.unpack(f"<{len(pb) // 4}i", pb)]))
            for cm in cmds:
                descs.append((cm.desc, ptr[n.inputs[0]], ptr[n.outputs[0]], i))
        name = f"ad_{c}"
        rows = []
        patch_in, patch_out = [], []
        for k, (w, xp, yp, i) in enumerate(descs):
            w = list(w)
            w[0] = 0                                               # NEXT is bound at run time
            rows.append("    {{" + ", ".join(_u(v) for v in w) + "}}")
            ref = f"{name}[{k}].w"
            if k + 1 < len(descs):
                bind.append(f"    {ref}[0] = MCUACCEL_ADDR(&{name}[{k + 1}]);")
            bind.append(f"    {ref}[13] += MCUACCEL_ADDR(aw_{i});")
            bind.append(f"    {ref}[15] += MCUACCEL_ADDR(ap_{i});")
            for word, base, patch in ((6, xp, patch_in), (11, yp, patch_out)):
                if base in ("input", "output"):
                    patch.append(f"    {ref}[{word}] = {_u(w[word])} + MCUACCEL_ADDR({base});")
                else:
                    bind.append(f"    {ref}[{word}] += MCUACCEL_ADDR({base});")
        ad_decl.append(f"static mcuaccel_desc_t {name}[{len(descs)}] = {{\n" + ",\n".join(rows) + "\n};\n")
        patches = patch_in + patch_out
        params = [s for s, pt in (("input", patch_in), ("output", patch_out)) if pt]
        decl = ", ".join({"input": "const int8_t *input", "output": "int8_t *output"}[s] for s in params) or "void"
        first, last = members[0], members[-1]
        funcs[first] = (f"chain_{c}_{first}_{last}", params,
                        f"static void chain_{c}_{first}_{last}({decl})\n{{\n    /* accelerated ops "
                        + ", ".join(f"{g.nodes[i].outputs[0]} = {g.nodes[i].op}" for i in members)
                        + " */\n" + "\n".join(patches) + (("\n" if patches else "") + f"    mcuaccel_run({name});\n}}\n"))

    out = ["/* Generated by mcuaccel_backend.emit -- do not edit. C99, no malloc, no libm, one static arena. */",
           '#include "model.h"', '#include "mcuaccel.h"', "", f"#define MCUACCEL_P {P}", "",
           "/* ---- int8 kernels (inlined from mcugrad/codegen/kernels/mg_int8.h) ---- */", hdr_src,
           "/* ---- kernel definitions (fallback ops only) ---- */"]
    out += [kfuncs[k] for k in used]
    if p.peak:
        out.append(f"""/* ---- scratch arena ({p.peak} bytes, 16-byte aligned) ---- */
#if defined(__GNUC__)
static int8_t mg_arena[MODEL_ARENA_BYTES] __attribute__((aligned(16)));
#else
static union {{ int8_t b[MODEL_ARENA_BYTES]; int64_t align; }} mg_arena_u;
#define mg_arena (mg_arena_u.b)
#endif
""")
    out.append("/* ---- weights and parameters (const) ---- */")
    out += consts
    out.append("/* ---- descriptor chains (address fields are offsets until bound) ---- */")
    out += ad_decl
    out.append("static int ad_bound;\n\nstatic void ad_bind(void)\n{\n    mcuaccel_check_p(MCUACCEL_P);\n" + "\n".join(bind) + "\n    ad_bound = 1;\n}\n")
    out.append("/* ---- one function per op / accelerated chain ---- */")
    body = []
    for i, n, acc in items:
        if acc:
            if i in funcs:
                fname, params, text = funcs[i]
                out.append(text)
                body.append(f"    {fname}({', '.join(params)});")
            continue
        fname = f"op_{i}_{n.op}"
        ins = ", ".join(n.inputs)
        params = [s for s in ("input", "output") if re.search(rf"\b{s}\b", fb[i])]
        decl = ", ".join({"input": "const int8_t *input", "output": "int8_t *output"}[s] for s in params) or "void"
        out.append(f"static void {fname}({decl})\n{{\n    /* {n.outputs[0]} = {n.op}({ins}) */\n    {fb[i]}\n}}\n")
        body.append(f"    {fname}({', '.join(params)});")
    out.append("void model_run(const int8_t *input, int8_t *output)\n{\n    if (!ad_bound) {\n        ad_bind();\n    }\n"
               + "\n".join(body) + "\n}\n")
    return {"model.h": files["model.h"], "model.c": "\n".join(out)}


def emit_dir(model: str, P: int, out: str) -> dict[str, str]:
    from .graphs import load_int8_graph
    files = emit_model(load_int8_graph(model), P, model)
    os.makedirs(out, exist_ok=True)
    for fn, text in files.items():
        with open(os.path.join(out, fn), "w") as f:
            f.write(text)
    return files


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", required=True, choices=["mnist_cnn", "cifar_mobilenet"])
    ap.add_argument("--P", type=int, required=True, choices=[1, 2, 4, 8, 16])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    emit_dir(a.model, a.P, a.out)
    print(os.path.join(a.out, "model.c"))
