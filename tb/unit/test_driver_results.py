"""Cheap checks of results/driver_overhead.json (T1.0) and of the driver sources (no simulation)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
J = ROOT / "results" / "driver_overhead.json"


def load():
    if not J.exists():
        pytest.skip("results/driver_overhead.json missing (run experiments/run_drv_timing.py)")
    return json.loads(J.read_text())


def test_driver_json_consistent():
    d = load()
    for k in ("drv_chain", "drv_patch", "drv_rdcycle", "commit", "date", "flags", "note",
              "poll_loop_string", "disassembly", "medians", "min", "max", "patch_slope_lsq"):
        assert k in d, k
    m = d["medians"]
    assert d["drv_rdcycle"] == m["rdcycle"] > 0
    assert d["drv_chain"] == m["chain"] - m["rdcycle"] > 0
    assert d["drv_patch"] == (m["p5"] - m["p1"]) // 4 > 0
    assert m["rdcycle"] < m["chain"] < m["p1"] < m["p2"] < m["p5"]       # more patches cost more
    assert abs(d["patch_slope_lsq"] - d["drv_patch"]) < 3
    for n in m:
        assert d["min"][n] <= m[n] <= d["max"][n]
    assert d["reps"] == 100 and "-O2" in d["flags"] and "rv32im" in d["flags"]
    assert "stub" in d["accelerator"]
    loop = d["poll_loop"]
    assert loop and loop[0].startswith("lw") and loop[-1].startswith("b")
    assert len(d["disassembly"]) > 10


def test_m1_uses_measured_driver_constants():
    d = load()
    import sys
    sys.path.insert(0, str(ROOT / "model"))
    import m1
    chain, patch, prov = m1.load_driver(ROOT / "results")
    assert (chain, patch, prov) == (d["drv_chain"], d["drv_patch"], "measured on stub")


@pytest.mark.skipif(shutil.which("riscv-none-elf-gcc") is None, reason="no riscv toolchain on PATH")
def test_driver_compiles_pedantic_c99(tmp_path):
    cmd = ["riscv-none-elf-gcc", "-std=c99", "-pedantic", "-Wall", "-Wextra", "-Werror", "-O2",
           "-march=rv32im_zicsr", "-mabi=ilp32", "-ffreestanding", "-c",
           str(ROOT / "sw/driver/mcuaccel.c"), "-o", str(tmp_path / "d.o")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
