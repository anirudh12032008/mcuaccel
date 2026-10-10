"""The two workload int8 graphs, built by exactly the pipeline of mcugrad bench/bench_codegen.py
`build()` (trained .mcg weights -> trace -> optimize -> collect_ranges(calib=512) ->
quantize(per_channel=True)), with mcugrad.seed(0) / random.seed(0) as in its main().
The pure-Python calibration takes minutes; the result is cached as Graph JSON in build/graphs/.
"""
from __future__ import annotations

import os
import random

ROOT = os.environ.get("MCUACCEL_ROOT") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MCUGRAD = os.path.join(ROOT, "third_party", "mcugrad")
CACHE = os.path.join(ROOT, "build", "graphs")
CALIB = 512
NORM = {"mnist": (0.1307, 0.3081), "cifar10": (0.4734, 0.2516)}
SPECS = {  # name -> (dataset, layout, weights file)
    "mnist_cnn": ("mnist", "NCHW", "mnist-sgd-s0.mcg"),
    "cifar_mobilenet": ("cifar10", "NHWC", "cifar10-sgd-s0.mcg"),
}


def build_int8_graph(model_name):
    import mcugrad
    from mcugrad import io, models
    from mcugrad.data import cifar10, mnist, to_float_batches
    from mcugrad.ir.passes import optimize
    from mcugrad.ir.trace import trace
    from mcugrad.quant import collect_ranges, quantize
    from mcugrad.tensor import Tensor

    dataset, layout, wfile = SPECS[model_name]
    mcugrad.seed(0)
    random.seed(0)
    load = mnist if dataset == "mnist" else cifar10
    tr_images, tr_labels = load(split="train")
    te_images, te_labels = load(split="test")
    model = models.MODELS[dataset][1](layout=layout)
    io.load_module(os.path.join(MCUGRAD, "results", "weights", wfile), models.State(model))
    model.eval()
    norm = NORM[dataset]
    flat, shape, _ = next(to_float_batches(te_images[:1], te_labels[:1], 1, layout, norm))
    g = optimize(trace(model, Tensor(flat, shape, layout=layout)), None)
    in_name = g.inputs[0]
    batches = ({in_name: (x, s)} for x, s, _ in
               to_float_batches(tr_images[:CALIB], tr_labels[:CALIB], 64, layout, norm))
    return quantize(g, collect_ranges(g, batches, CALIB), per_channel=True)


def load_int8_graph(model_name):
    from mcugrad.ir.nodes import Graph
    path = os.path.join(CACHE, model_name + ".json")
    if os.path.exists(path):
        with open(path) as f:
            return Graph.from_json(f.read())
    g = build_int8_graph(model_name)
    os.makedirs(CACHE, exist_ok=True)
    with open(path + ".tmp", "w") as f:
        f.write(g.to_json())
    os.replace(path + ".tmp", path)
    return g
