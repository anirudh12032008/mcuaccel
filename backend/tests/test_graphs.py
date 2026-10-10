import os

import pytest
from mcuaccel_backend.graphs import MCUGRAD, load_int8_graph
from mcuaccel_backend.layers import from_node, supported
from mcugrad.codegen import emit

MODELS = ["mnist_cnn", "cifar_mobilenet"]


@pytest.mark.slow
@pytest.mark.parametrize("name", MODELS)
def test_model_c_reproduced(name):
    g = load_int8_graph(name)                      # cached under build/graphs/ after the first build
    got = emit(g, model_name=name, quant_name="int8_pc")["model.c"]
    with open(os.path.join(MCUGRAD, "firmware", "generated", name, "model.c")) as f:
        assert got == f.read()


@pytest.mark.slow
def test_accelerated_ops():
    modes = {}
    for name in MODELS:
        g = load_int8_graph(name)
        modes[name] = [from_node(g, n).mode for n in g.nodes
                       if n.op in ("conv2d", "conv2d_fused", "matmul", "matmul_fused")]
        for n in g.nodes:
            if n.op in ("conv2d", "conv2d_fused", "matmul", "matmul_fused"):
                assert supported(g, n) is True, (name, n.outputs, supported(g, n))
    assert modes["mnist_cnn"] == ["conv", "conv", "fc"]
    assert modes["cifar_mobilenet"] == ["conv"] + ["dw", "conv"] * 6 + ["fc"]
