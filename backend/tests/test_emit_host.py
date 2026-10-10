"""Emitted model.c on the host (DESIGN 10.3, T4.1): compiled -Werror -Wall -Wextra against a host
accelerator stub whose mcuaccel_run hands the descriptor chain to the Python executor (ctypes
callback). Addresses are offsets into a 32 MiB window around an anchor symbol of the same shared
object, so arena, weights, descriptors and the caller's buffers are one flat memory for exec.py.
Also: fairness (DESIGN 6.5) -- emitted fallback kernels / op functions equal the baseline's text."""
import ctypes
import os
import re
import subprocess
from array import array

import pytest
import struct

from mcuaccel_backend.emit import emit_model, plan
from mcuaccel_backend.exec import _run_one
from mcuaccel_backend.graphs import MCUGRAD, ROOT, load_int8_graph
from mcugrad.quant import run_int8

GEN = os.path.join(MCUGRAD, "firmware", "generated")
WIN = 16 << 20

STUB = r"""
#include <stdint.h>
#include <string.h>
#include "model.h"
#include "mcuaccel.h"
static char anchor[16];
static int8_t in_buf[MODEL_INPUT_BYTES + 64], out_buf[MODEL_OUTPUT_BYTES + 64];
uint32_t host_p = 0xFFFFFFFFu;
void mcuaccel_check_p(uint32_t p) { host_p = p; }
static void (*cb)(uint32_t);
uint32_t host_addr(const void *p) { return (uint32_t)((uintptr_t)p - ((uintptr_t)anchor - %d)); }
uintptr_t host_base(void) { return (uintptr_t)anchor - %d; }
void host_set_cb(void (*f)(uint32_t)) { cb = f; }
void mcuaccel_run(volatile mcuaccel_desc_t *first) { cb(host_addr((const void *)first)); }
void host_infer(const int8_t *x, int8_t *y, int in_off, int out_off)
{
    memset(in_buf, 0x55, sizeof in_buf);
    memset(out_buf, 0x55, sizeof out_buf);
    memcpy(in_buf + in_off, x, MODEL_INPUT_BYTES);
    model_run(in_buf + in_off, out_buf + out_off);
    memcpy(y, out_buf + out_off, MODEL_OUTPUT_BYTES);
}
""" % (WIN, WIN)
CB = ctypes.CFUNCTYPE(None, ctypes.c_uint32)


def _cc():
    return os.environ.get("CC") or "cc"


def _build(tmp, model, P):
    g = load_int8_graph(model)
    for fn, text in emit_model(g, P, model).items():
        (tmp / fn).write_text(text)
    (tmp / "stub.c").write_text(STUB)
    so = tmp / "m.so"
    drv = os.path.join(ROOT, "sw", "driver")
    cmd = [_cc(), "-std=c99", "-O1", "-Werror", "-Wall", "-Wextra", "-shared", "-fPIC", f"-I{tmp}", f"-I{drv}",
           "-DMCUACCEL_ADDR(p)=host_addr(p)", "-include", str(tmp / "hostaddr.h"),
           "-o", str(so), str(tmp / "model.c"), str(tmp / "stub.c")]
    (tmp / "hostaddr.h").write_text("#include <stdint.h>\nuint32_t host_addr(const void *p);\n")
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0 and not r.stderr, r.stderr
    return g, ctypes.CDLL(str(so))


def _images(model, n):
    """First n quantised test images, from the committed baseline testset.c (read only its head)."""
    g = load_int8_graph(model)
    nb = 1
    for d in g.values[g.inputs[0]].shape:
        nb *= d
    with open(os.path.join(GEN, model, "testset.c")) as f:
        head = f.read(n * nb * 6 + 4096).split("= {", 1)[1]
    v = [int(t) for t in re.findall(r"-?\d+", head)][: n * nb]
    assert len(v) == n * nb
    return [array("b", v[i * nb:(i + 1) * nb]) for i in range(n)]


IN_OFF = [8, 13, 4, 31]       # odd offsets hit the aligner on a conv-mode input
OUT_OFF = [8, 21, 12, 3]
GUARD = 8


def _walk(mem, addr, P, trace, errors):
    """Run the chain one descriptor at a time (so X reads can be checked against that command's x
    buffer); exceptions are recorded, not swallowed by ctypes."""
    try:
        while addr:
            n0 = len(trace)
            _run_one(mem, addr, P, trace)
            d = struct.unpack_from("<17I", mem, addr)
            H, W, C = d[2] & 0xFFFF, d[2] >> 16, d[4] & 0xFFFF
            lo = d[6] + (d[1] >> 20 & 15) * d[8] + (d[1] >> 24 & 15) * d[7]    # X_ORIGIN includes -PH*XROW -PW*XPIX
            hi = lo + (H - 1) * d[8] + (W - 1) * d[7] + C
            for t in trace[n0:]:
                if t[0] == "X" and not (lo - GUARD <= t[1] and t[1] + 3 <= hi + GUARD):
                    errors.append(f"X read {t[1]:#x} outside [{lo:#x}, {hi:#x}] +-{GUARD}")
            addr = d[0]
    except Exception as e:  # noqa: BLE001
        errors.append(repr(e))


def _check(tmp_path, model, P, n):
    g, lib = _build(tmp_path, model, P)
    lib.host_base.restype = ctypes.c_size_t
    mem = memoryview((ctypes.c_char * (2 * WIN)).from_address(lib.host_base())).cast("B")
    calls, errors = [], []

    def run(addr):
        calls.append(addr)
        _walk(mem, addr, P, [], errors)

    cb = CB(run)
    lib.host_set_cb(cb)
    shape = g.values[g.inputs[0]].shape
    nout = len(run_int8(g, {g.inputs[0]: (_images(model, 1)[0], shape)})[g.outputs[0]])
    for k, x in enumerate(_images(model, n)):
        want = run_int8(g, {g.inputs[0]: (x, shape)})[g.outputs[0]]
        out = (ctypes.c_int8 * nout)()
        xb = (ctypes.c_int8 * len(x)).from_buffer_copy(x.tobytes())
        lib.host_infer(xb, out, IN_OFF[k % len(IN_OFF)], OUT_OFF[k % len(OUT_OFF)])
        assert not errors, errors[:3]
        assert bytes(out) == want.tobytes(), f"image {k}"
    assert len(calls) == n * len(plan(g)[1])
    assert ctypes.c_uint32.in_dll(lib, "host_p").value == P


@pytest.mark.parametrize("P", [8, 2])
def test_mnist_host_bitexact(tmp_path, P):
    _check(tmp_path, "mnist_cnn", P, 3)


def test_cifar_host_bitexact_one_image(tmp_path):
    _check(tmp_path, "cifar_mobilenet", 8, 1)


@pytest.mark.slow
@pytest.mark.parametrize("P,n", [(8, 2), (2, 2)])    # P=2 splits conv layers into several commands
def test_cifar_host_bitexact(tmp_path, P, n):
    _check(tmp_path, "cifar_mobilenet", P, n)


def _funcs(text):
    """{name: definition text} of top-level static functions (definition starts at column 0, ends at '}')."""
    return {m.group(1): m.group(0) for m in
            re.finditer(r"^static (?:inline )?[\w\s\*]*?\b(\w+)\([^;{]*?\)\n?\{.*?^\}\n", text, re.S | re.M)}


@pytest.mark.parametrize("model", ["mnist_cnn", "cifar_mobilenet"])
def test_fallback_text_identical_to_baseline(model):
    g = load_int8_graph(model)
    mine = _funcs(emit_model(g, 8, model)["model.c"])
    base = _funcs(open(os.path.join(GEN, model, "model.c")).read())
    shared = [k for k in mine if k.startswith(("mg_", "op_"))]
    assert any(k.startswith("mg_") for k in shared) and any(k.startswith("op_") for k in shared)
    for k in shared:
        assert mine[k] == base[k], k
    items, chains, _ = plan(g)
    acc = {f"op_{i}_{nd.op}" for i, nd, a in items if a}
    ops = lambda d: {k for k in d if k.startswith("op_")}  # noqa: E731
    assert ops(mine) == ops(base) - acc
    # call order: baseline order with each chain's ops collapsed into one chain_ call at its first op
    chain_at = {c[0]: f"chain_{k}_{c[0]}_{c[-1]}" for k, c in enumerate(chains)}
    inner = {i for c in chains for i in c[1:]}
    names = {f"op_{i}_{nd.op}": i for i, nd, _ in items}
    calls = lambda t: re.findall(r"\b((?:op|chain)_\d+_\w+)\(", t.split("void model_run(")[1])  # noqa: E731
    want = [chain_at.get(names[c], c) for c in calls(open(os.path.join(GEN, model, "model.c")).read())
            if names[c] not in inner]
    assert calls(emit_model(g, 8, model)["model.c"]) == want
