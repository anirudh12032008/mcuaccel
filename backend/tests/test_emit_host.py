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
from mcuaccel_backend.emit import emit_model
from mcuaccel_backend.exec import run_descriptor
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
static int8_t in_buf[MODEL_INPUT_BYTES + 16], out_buf[MODEL_OUTPUT_BYTES + 16];
static void (*cb)(uint32_t);
uint32_t host_addr(const void *p) { return (uint32_t)((uintptr_t)p - ((uintptr_t)anchor - %d)); }
uintptr_t host_base(void) { return (uintptr_t)anchor - %d; }
void host_set_cb(void (*f)(uint32_t)) { cb = f; }
void mcuaccel_run(volatile mcuaccel_desc_t *first) { cb(host_addr((const void *)first)); }
void host_infer(const int8_t *x, int8_t *y)
{
    memcpy(in_buf + 8, x, MODEL_INPUT_BYTES);
    model_run(in_buf + 8, out_buf + 8);
    memcpy(y, out_buf + 8, MODEL_OUTPUT_BYTES);
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


def _check(tmp_path, model, P, n):
    g, lib = _build(tmp_path, model, P)
    lib.host_base.restype = ctypes.c_size_t
    mem = memoryview((ctypes.c_char * (2 * WIN)).from_address(lib.host_base())).cast("B")
    calls = []
    cb = CB(lambda addr: (calls.append(addr), run_descriptor(mem, addr, P)))
    lib.host_set_cb(cb)
    shape = g.values[g.inputs[0]].shape
    nout = len(run_int8(g, {g.inputs[0]: (_images(model, 1)[0], shape)})[g.outputs[0]])
    for x in _images(model, n):
        want = run_int8(g, {g.inputs[0]: (x, shape)})[g.outputs[0]]
        out = (ctypes.c_int8 * nout)()
        xb = (ctypes.c_int8 * len(x)).from_buffer_copy(x.tobytes())
        lib.host_infer(xb, out)
        assert bytes(out) == want.tobytes()
    assert len(calls) == n * g_chains(g)


def g_chains(g):
    """Accelerator chains per inference = maximal runs of accelerated ops."""
    from mcuaccel_backend.layers import supported
    ok = [supported(g, nd) is True for nd in g.nodes if nd.op not in ("reshape", "flatten")]
    return sum(1 for i, a in enumerate(ok) if a and (i == 0 or not ok[i - 1]))


@pytest.mark.parametrize("P", [8, 2])
def test_mnist_host_bitexact(tmp_path, P):
    _check(tmp_path, "mnist_cnn", P, 3)


@pytest.mark.slow
def test_cifar_host_bitexact(tmp_path):
    _check(tmp_path, "cifar_mobilenet", 8, 2)


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
