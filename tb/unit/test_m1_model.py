"""M1 model tests: layer table vs model.c, DESIGN hand checks, sanity properties."""
import re
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
import m1  # noqa: E402

MODELS = list(m1.LAYERS)


def layer(model, op):
    return next(L for L in m1.LAYERS[model] if L["op"] == op)


def parse_model_c(model):
    """{op: dict} from the mg_* calls inside the op_* functions of the generated model.c."""
    src = (ROOT / f"third_party/mcugrad/firmware/generated/{model}/model.c").read_text()
    out = {}
    for fm in re.finditer(r"static void op_(\d+)_\w+\([^)]*\)\s*\{(.*?)\n\}", src, re.S):
        calls = re.findall(r"\bmg_(\w+)\(([^;]*)\);", fm.group(2))
        assert len(calls) == 1, fm.group(1)
        name, args = calls[0]
        a = [x.strip() for x in args.split(",")]
        i = lambda k: int(a[k])
        if name == "conv2d_nhwc_s8":
            d = dict(kind="conv", H=i(2), W=i(3), C=i(4), O=i(6), KH=i(7), KW=i(8), SH=i(11),
                     SW=i(12), PH=i(13), PW=i(14), groups=i(15), x_zp=i(16))
        elif name == "conv1x1_nhwc_s8":   # no x_zp / pad arguments in the call
            d = dict(kind="conv", H=i(2), W=i(3), C=i(4), O=i(6), KH=1, KW=1, SH=i(9), SW=i(10),
                     PH=0, PW=0, groups=1)
        elif name == "dwconv2d_nhwc_s8":
            d = dict(kind="dw", H=i(2), W=i(3), C=i(4), O=i(4), KH=i(6), KW=i(7), SH=i(10),
                     SW=i(11), PH=i(12), PW=i(13), groups=i(4), x_zp=i(14))
        elif name == "fc_s8":             # no x_zp in the call
            d = dict(kind="fc", H=1, W=1, C=i(2), O=i(4), KH=1, KW=1, SH=1, SW=1, PH=0, PW=0,
                     groups=1)
        elif name in ("maxpool2d_nhwc_s8", "avgpool2d_nhwc_s8"):
            d = dict(kind=name[:7], H=i(2), W=i(3), C=i(4), O=i(4), KH=i(6), KW=i(7), SH=i(8),
                     SW=i(9), PH=i(10), PW=i(11))
        elif name == "transpose_nchw_to_nhwc_s8":
            d = dict(kind="transpose", C=i(2), H=i(3), W=i(4), O=i(2))
        else:
            raise AssertionError(name)
        out[int(fm.group(1))] = d
    return out


@pytest.mark.parametrize("model", MODELS)
def test_layers_match_model_c(model):
    parsed = parse_model_c(model)
    assert sorted(parsed) == [L["op"] for L in m1.LAYERS[model]]
    for L in m1.LAYERS[model]:
        for k, v in parsed[L["op"]].items():
            assert L[k] == v, (model, L["op"], k, L[k], v)


def test_total_macs():
    tot = {m: sum(r["macs"] for r in m1.predict(m, "os", 8)) for m in MODELS}
    assert tot == {"mnist_cnn": 266432, "cifar_mobilenet": 1776560}


def test_t_tile_examples():
    cfg = m1.Cfg()
    for op, want, W in ((2, 8, 2), (12, 34, 2)):   # DESIGN 7.2: aligned 1x1, P=8
        L = layer("cifar_mobilenet", op)
        tiles = []
        m1._cmd_os(L, 8, cfg, 0, 8 * (L["O"] // 8), tiles)
        assert tiles[0]["NV"] == 8 and tiles[0]["W"][0, 0] == W
        assert (tiles[0]["T"] == want).all()


def test_R_worked_example_op0():
    # DESIGN 6.4: pixel (0,0) of cifar op_0, aligned input: 2 rows x (1 prime + 3 quads) = 8
    L = layer("cifar_mobilenet", 0)
    tiles = []
    m1._cmd_os(L, 8, m1.Cfg(), 0, 16, tiles)
    assert tiles[0]["R"][0, 0] == 8


def _R_scalar(L, x_base):
    """Literal 7.1 loops, scalar."""
    OH, OW = m1.out_hw(L)
    R = np.zeros((OH, OW), dtype=np.int64)
    C = L["C"]
    for oy in range(OH):
        for ox in range(OW):
            iy0, ix0 = oy * L["SH"] - L["PH"], ox * L["SW"] - L["PW"]
            ky0, ky1 = max(0, -iy0), min(L["KH"], L["H"] - iy0)
            kx0, kx1 = max(0, -ix0), min(L["KW"], L["W"] - ix0)
            if L["kind"] == "dw":
                R[oy, ox] = (ky1 - ky0) * (kx1 - kx0)
                continue
            j0, j1 = (kx0 * C) >> 2, (kx1 * C + 3) >> 2
            xorg = x_base - L["PH"] * L["W"] * C - L["PW"] * C
            for ky in range(ky0, ky1):
                rowA = xorg + oy * L["SH"] * L["W"] * C + ox * L["SW"] * C + ky * L["W"] * C
                R[oy, ox] += (j1 - j0) + ((rowA + 4 * j0) % 4 != 0)
    return R


@pytest.mark.parametrize("xa", [0, 1, 2, 3])
def test_R_vectorised_equals_scalar(xa):
    for model in MODELS:
        for L in m1.LAYERS[model]:
            if L["kind"] in m1.ACCEL_KINDS:
                xb = xa if L.get("from_input") else 0
                assert (m1._R(L, m1._geom(L), xb) == _R_scalar(L, xb)).all(), (model, L["op"])


def test_t_compute_monotone_in_P_os():
    for model in MODELS:
        tc = {}
        for P in m1.PS:
            for r in m1.predict(model, "os", P):
                tc[r["op"], P] = tc.get((r["op"], P), 0) + r["t_compute"]
        for op in {o for o, _ in tc}:
            seq = [tc[op, P] for P in m1.PS]
            assert all(a >= b for a, b in zip(seq, seq[1:])), (model, op, seq)


def test_acc_bus_rd_le_os():
    for model in MODELS:
        for P in m1.PS:
            os_, acc = m1.predict(model, "os", P), m1.predict(model, "acc", P)
            for a, b in zip(acc, os_):
                assert (a["op"], a["cmd"]) == (b["op"], b["cmd"])
                assert a["bus_rd"] <= b["bus_rd"], (model, P, a["op"])


def test_summary_and_pstar_smoke(tmp_path):
    s = m1.run_all(tmp_path)   # no baseline files: speedups null
    e = s["models"]["cifar_mobilenet"]["archs"]["os"]
    assert e["P"]["8"]["predicted_speedup"] is None
    assert e["P"]["8"]["n_chains"] == 2 and e["p_star"] in m1.PS
    assert (tmp_path / "m1_predictions.csv").exists()


def _write_results(d, base, ops, window=120, drv=None):
    import json
    for m in MODELS:
        (d / f"baseline_{m}.json").write_text(json.dumps(
            {"mcugrad_result": {"cycles_median": base[m]}, "x_align": 0, "images": 10, "runs": 10}))
        (d / f"baseline_profile_{m}.json").write_text(json.dumps(
            {"ops": [{"op": o, "name": "x", "kernel": "k", "cycles_per_inference": c,
                      "share": 0.0} for o, c in ops[m].items()]}))
    (d / "baseline_overhead.json").write_text(json.dumps({"cycles_median": window}))
    if drv:
        (d / "driver_overhead.json").write_text(json.dumps(drv))


SYN_OPS = {"mnist_cnn": {0: 100.5, 1: 9e6, 2: 2000.25, 3: 9e6, 4: 3000.0, 5: 400.0, 7: 5e5},
           "cifar_mobilenet": {**{i: 1e6 for i in range(13)}, 13: 777.5, 15: 3e4}}
SYN_BASE = {"mnist_cnn": 12_000_000, "cifar_mobilenet": 70_000_000}


def test_summary_software_term_is_profile_sum_of_fallback_ops(tmp_path):
    _write_results(tmp_path, SYN_BASE, SYN_OPS, drv={"drv_chain": 70, "drv_patch": 9})
    s = m1.run_all(tmp_path)
    fallback = {"mnist_cnn": (0, 2, 4, 5), "cifar_mobilenet": (13,)}
    assert s["inputs"]["driver"] == "measured on stub"
    for m, ops in fallback.items():
        assert sorted(L["op"] for L in m1.LAYERS[m] if L["kind"] not in m1.ACCEL_KINDS) == sorted(ops)
        want = sum(SYN_OPS[m][o] for o in ops)       # conv / fc ops are NOT in the software term
        for P in m1.PS:
            e = s["models"][m]["archs"]["os"]["P"][str(P)]
            assert e["sw_cycles"] == pytest.approx(want)
            assert e["sw_complete"] and e["driver_cycles"] == 70 * e["n_chains"] + 9 * e["n_patches"]
            assert e["predicted_model_run"] == pytest.approx(
                e["sw_cycles"] + e["accel_cycles"] + e["driver_cycles"] + 120)
            assert e["accel_plus_driver_cycles"] == e["accel_cycles"] + e["driver_cycles"]
            assert e["predicted_speedup"] == pytest.approx(SYN_BASE[m] / e["predicted_model_run"])


def test_real_results_software_term_and_patches():
    """The committed results/: fallback sum equals the profile JSON; patch counts at P=8."""
    import json
    res = ROOT / "results"
    if not (res / "baseline_profile_mnist_cnn.json").exists():
        pytest.skip("no profile results")
    for m, ops in (("mnist_cnn", (0, 2, 4, 5)), ("cifar_mobilenet", (13,))):
        prof = json.loads((res / f"baseline_profile_{m}.json").read_text())
        want = sum(o["cycles_per_inference"] for o in prof["ops"] if o["op"] in ops)
        cyc, got, xa, _ = m1.load_baseline(m, res)
        assert sum(got[o] for o in ops) == pytest.approx(want) and xa == 0 and cyc > 0
        e = m1.summarize(m, "os", 8, m1.Cfg(), got, cyc)
        assert e["sw_cycles"] == pytest.approx(want)
    # patched descriptor fields per call at P=8 (DESIGN 6.5): mnist writes `output` once (op_7,
    # its op_1 reads the arena, not `input`); cifar reads `input` (op_0) and writes `output` (op_15)
    pat = {m: m1.summarize(m, "os", 8)["n_patches"] for m in MODELS}
    assert pat == {"mnist_cnn": 1, "cifar_mobilenet": 2}


def test_pstar_uses_sum_stamp(tmp_path):
    s = m1.run_all(tmp_path)
    for m in MODELS:
        e = s["models"][m]["archs"]["os"]
        st = {p: e["P"][str(p)]["sum_stamp"] for p in m1.PS}
        assert e["p_star"] == min(p for p in st if st[p] <= 1.10 * st[16])
        assert e["P"]["8"]["sum_stamp"] == sum(r["t_cmd"] for r in m1.predict(m, "os", 8))
