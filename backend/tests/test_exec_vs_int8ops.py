"""Executor == int8ops on seeded random layers (DESIGN §10.2). MCUACCEL_SEEDS=1000 for the full run."""
import os

import pytest
from helpers import gen_layer, image, reference
from mcuaccel_backend.exec import run_descriptor
from mcuaccel_backend.layers import Layer, check
from mcuaccel_backend.split import split_plan

SEEDS = int(os.environ.get("MCUACCEL_SEEDS", "300"))


def run_and_compare(L, P, xa, ya, x, seed=0):
    assert check(L) is True
    mem, y_addr, ny, d_addr, cmds = image(L, P, xa, ya, x, seed)
    before = bytes(mem)
    stats = run_descriptor(mem, d_addr, P, stats=True)
    assert len(stats) == len(cmds)
    assert bytes(mem[y_addr:y_addr + ny]) == reference(L, x).tobytes()
    assert bytes(mem[:y_addr]) == before[:y_addr] and bytes(mem[y_addr + ny:]) == before[y_addr + ny:]
    assert sum(s["out_bytes"] for s in stats) == ny
    return stats, cmds


@pytest.mark.parametrize("seed", range(SEEDS))
def test_random_layer(seed):
    L, P, xa, ya, x = gen_layer(seed)
    run_and_compare(L, P, xa, ya, x, seed)


def _fixed(mode, H, W, C, O, K, P, seed):
    import numpy as np
    from array import array
    r = np.random.default_rng(seed)
    Cg = 1 if mode == "dw" else C
    w = array("b", r.integers(-128, 128, O * K * K * Cg).astype(np.int8).tobytes())
    L = Layer(mode, H, W, C, O, K, K, 1, 1, 0, 0, -5, 7, -128, 127, w,
              [int(b) for b in r.integers(-5000, 5000, O)], [int(m) for m in r.integers(1 << 30, 1 << 31, O)],
              [int(s) for s in r.integers(-10, 0, O)])
    return L, r.integers(-128, 128, H * W * C).astype("int8").tobytes()


@pytest.mark.parametrize("mode,C,O,P,ncmd", [("conv", 20, 40, 8, 2), ("conv", 20, 40, 4, 3), ("dw", 96, 96, 8, 2)])
def test_forced_splits(mode, C, O, P, ncmd):
    # 5x5, C=20: 125 rows per tile -> 4 tiles per command (32 channels at P=8); dw 5x5: 80 channels
    L, x = _fixed(mode, 5, 5, C, O, 5, P, 1)
    assert len(split_plan(L, P)) == ncmd
    run_and_compare(L, P, 1 if mode == "conv" else 0, 3, x)
