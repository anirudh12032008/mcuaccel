"""C2b: whole networks layer by layer (DESIGN 10.2).

For each model (mnist_cnn, cifar_mobilenet) and N test images (quantized exactly as mcugrad's
bench_codegen / sw/gen_testset.py do), the int8 graph runs node by node:
  - accelerated nodes (`layers.supported(graph, node) is True`: conv, dw, fc): on the RTL through
    the harness, x = the current tensor placed in the RAM image;
  - all other nodes (and every node of the reference path): `mcugrad.quant.sim.run_int8` on a
    one-node copy of the graph, so the dispatch/attrs are sim.py's own.
Intermediate tensors: the reference path (pure int8ops, node by node) keeps every tensor in
`ref_env`, the RTL path keeps `rtl_env`; EVERY node output is compared between the two, each
accelerated node is also checked against int8ops on the RTL path's own input (isolates the first
bad layer), and the final outputs are compared with one monolithic `sim.run_int8` call.
Default 2 images (RTL is slow); MCUACCEL_FULL=1: 10; MCUACCEL_IMAGES overrides.
"""
import copy
import json
import os
import sys
from array import array
from pathlib import Path

import cocotb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from accel import harness as H  # noqa: E402
from accel.gen_layers import ROOT  # noqa: E402

pytestmark = H.PYTESTMARK
MODELS = ["mnist_cnn", "cifar_mobilenet"]
ACCEL_OPS = ("conv2d", "conv2d_fused", "matmul", "matmul_fused")


def n_images():
    return H.env_int("MCUACCEL_IMAGES", 10 if os.environ.get("MCUACCEL_FULL") == "1" else 2)


def graph_cache(name):
    return ROOT / "build" / "graphs" / f"{name}.json"


def run_node(graph, node, env, sim):
    """sim.run_int8's own dispatch for one node: returns {output: array}."""
    g = copy.copy(graph)
    g.nodes, g.outputs = [node], list(node.outputs)
    ins = {n: (env[n], graph.values[n].shape) for n in node.inputs if n not in graph.consts}
    return sim.run_int8(g, ins)


def quantized_images(name, graph, n):
    """First n test images quantized like bench_codegen.build (scale/zp from the graph input)."""
    from mcuaccel_backend.graphs import NORM, SPECS
    from mcugrad.data import cifar10, mnist, to_float_batches
    from mcugrad.quant import quantize_input
    dataset, layout, _ = SPECS[name]
    images, labels = (mnist if dataset == "mnist" else cifar10)(split="test")
    q = graph.values[graph.inputs[0]].quant
    scale, zp = q["scale"][0], q["zero_point"][0]
    out = []
    for flat, shape, _ in to_float_batches(images[:n], labels[:n], 1, layout, NORM[dataset]):
        out.append((quantize_input(flat, scale, zp), shape))
    return out


@cocotb.test()
async def whole_network(dut):
    from mcuaccel_backend.graphs import load_int8_graph
    from mcuaccel_backend.layers import from_node, supported
    from mcugrad.quant import sim
    b = await H.Bench.create(dut)
    nimg = n_images()
    models = [m for m in os.environ.get("MCUACCEL_MODELS", ",".join(MODELS)).split(",") if m]
    report = {}
    bad = {"bytes": 0}                      # byte-level tally for C2b (results/m5_bitexact.json)

    def nbad(a, c):
        return sum(1 for u, v in zip(a, c) if u != v) + abs(len(a) - len(c))

    nodes_csv = H.OUT / "m3_network_nodes.csv"
    nodes_csv.write_text("model,image,node,op,accelerated,cycles,mismatches\n")
    for name in models:
        if not graph_cache(name).exists():
            dut._log.warning("SKIP %s: int8 graph cache %s missing (pure-Python calibration takes "
                             "tens of minutes; build it with backend load_int8_graph)", name,
                             graph_cache(name))
            report[name] = "skipped: graph cache missing"
            continue
        g = load_int8_graph(name)
        imgs = quantized_images(name, g, nimg)
        in_name = g.inputs[0]
        nacc = 0
        for ii, (xq, shape) in enumerate(imgs):
            ref_env = {k: c.data for k, c in g.consts.items()}
            rtl_env = dict(ref_env)
            ref_env[in_name] = rtl_env[in_name] = xq
            local_bad = 0
            for ni, node in enumerate(g.nodes):
                ref_out = run_node(g, node, ref_env, sim)
                ref_env.update(ref_out)
                accel = node.op in ACCEL_OPS and supported(g, node) is True
                cyc = mm_here = 0
                if accel:
                    nacc += 1
                    L = from_node(g, node)
                    x = array("b", rtl_env[node.inputs[0]]).tobytes()
                    y, res, img = await b.run_layer(L, x, xa=(ni % 3) if L.mode != "dw" else 0,
                                                    ya=ni % 4, stalls=(ii % 2 == 1), seed=ii * 1000 + ni)
                    b.check_normal(res, img, f"{name} img {ii} node {ni} ({node.op})")
                    cyc, mm_here = res.cycles, res.mismatches
                    bad["bytes"] += mm_here
                    # RTL vs int8ops on the RTL path's own input
                    loc = run_node(g, node, rtl_env, sim)[node.outputs[0]]
                    ok = array("b", y) == loc
                    local_bad += not ok
                    bad["bytes"] += nbad(array("b", y), loc)
                    b.check(ok, f"{name} img {ii} node {ni}: RTL output != int8ops on same input")
                    rtl_env[node.outputs[0]] = array("b", y)
                else:
                    rtl_env.update(run_node(g, node, rtl_env, sim))
                for o in node.outputs:       # every tensor of the reference path
                    bad["bytes"] += nbad(rtl_env[o], ref_env[o])
                    b.check(rtl_env[o] == ref_env[o],
                            f"{name} img {ii}: tensor {o} (node {ni}, {node.op}) differs from the reference run")
                with open(nodes_csv, "a") as f:
                    f.write(f"{name},{ii},{ni},{node.op},{int(accel)},{cyc},{mm_here}\n")
                dut._log.info("%s img %d node %d %s accel=%d cycles=%d", name, ii, ni, node.op, accel, cyc)
            full = sim.run_int8(g, {in_name: (xq, shape)})     # monolithic reference
            for o in g.outputs:
                b.check(full[o] == ref_env[o], f"{name} img {ii}: node-by-node reference != run_int8")
                bad["bytes"] += nbad(full[o], rtl_env[o])
                b.check(full[o] == rtl_env[o], f"{name} img {ii}: final output {o} differs from run_int8")
            dut._log.info("%s img %d: final output %s, argmax %d, node-local mismatches %d", name, ii,
                          list(rtl_env[g.outputs[0]]), max(range(len(rtl_env[g.outputs[0]])),
                          key=lambda k: rtl_env[g.outputs[0]][k]), local_bad)
        report[name] = f"{len(imgs)} images, {nacc} accelerated node executions"
        report.setdefault("_images", {})[name] = len(imgs)
    report["_mismatch_bytes"] = bad["bytes"]
    (H.OUT / "whole_network_report.json").write_text(json.dumps(report, indent=1))
    b.finish("whole_network")


def test_whole_network():
    avail = [m for m in MODELS if graph_cache(m).exists()]
    for m in MODELS:
        if m not in avail:
            print(f"WHOLE_NETWORK: {m} SKIPPED: {graph_cache(m)} missing (int8 graph not built)")
    if not avail:
        pytest.skip("no int8 graph cache under build/graphs/")
    for P in H.ps_list()[:1]:
        H.run_sim("test_whole_network", P=P, env={"MCUACCEL_MODELS": ",".join(avail)})
    s = H.read_summary("whole_network")
    print("WHOLE_NETWORK:", json.loads((H.OUT / "whole_network_report.json").read_text()),
          f"problems={len(s['problems'])}")
    if not s["stub"]:
        assert not s["problems"]
