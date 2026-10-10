#!/usr/bin/env python3
"""Reduced test set for the RAM-limited SoC: first N test images quantized exactly as
third_party/mcugrad/bench/bench_codegen.py does, emitted with mcugrad.codegen.emit_testset.
    gen_testset.py <mnist_cnn|cifar_mobilenet> [--n 10] [--out build/fw/<model>]
The result is cross-checked against the first N images of the committed generated testset.c."""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "third_party", "mcugrad"))
from mcugrad.codegen import emit_testset  # noqa: E402
from mcugrad.data import cifar10, mnist, to_float_batches  # noqa: E402
from mcugrad.quant import quantize_input  # noqa: E402

NORM = {"mnist": (0.1307, 0.3081), "cifar10": (0.4734, 0.2516)}  # bench/train.py
SPEC = {"mnist_cnn": ("mnist", "NCHW"), "cifar_mobilenet": ("cifar10", "NHWC")}
GEN = os.path.join(ROOT, "third_party", "mcugrad", "firmware", "generated")


def gen(model: str, n: int, out: str) -> None:
    ds, layout = SPEC[model]
    h = open(os.path.join(GEN, model, "model.h")).read()
    scale = float(re.search(r"MODEL_INPUT_SCALE\s+\(?(-?[\d.eE+-]+)f?\)?", h).group(1))
    zp = int(re.search(r"MODEL_INPUT_ZP\s+\(?(-?\d+)\)?", h).group(1))
    images, labels = (mnist if ds == "mnist" else cifar10)(split="test")
    xs = [quantize_input(x, scale, zp)
          for x, _, _ in to_float_batches(images[:n], labels[:n], 1, layout, NORM[ds])]
    files = emit_testset(xs, labels[:n].tolist(), n)
    # cross-check against the committed full test set (same images, same quantization)
    old = open(os.path.join(GEN, model, "testset.c")).read().split("testset_labels")[0]
    old_vals = [int(v) for v in re.findall(r"-?\d+", old.split("= {", 1)[1])][: n * len(xs[0])]
    assert old_vals == [v for x in xs for v in x], "regenerated images differ from committed testset.c"
    c = files["testset.c"]   # emit_testset has no alignment attribute; model_run/bench read words
    c = c.replace("const int8_t testset_images", "const int8_t __attribute__((aligned(4))) testset_images")
    files["testset.c"] = c.replace("const uint8_t testset_labels", "const uint8_t __attribute__((aligned(4))) testset_labels")
    os.makedirs(out, exist_ok=True)
    for fn, text in files.items():
        with open(os.path.join(out, fn), "w") as f:
            f.write(text)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("model", choices=SPEC)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--out")
    a = ap.parse_args()
    gen(a.model, a.n, a.out or os.path.join(ROOT, "build", "fw", a.model))
