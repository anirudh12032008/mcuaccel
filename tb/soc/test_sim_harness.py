"""End-to-end: Verilator harness + mnist baseline firmware (2 images, 1 run) on the SoC."""
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "sw"))


def test_mnist_baseline_on_soc():
    for f in ("rtl/soc/soc_top.sv", "sw/common/crt0.S", "sw/common/link.ld"):
        if not os.path.exists(os.path.join(ROOT, f)):
            pytest.skip(f"{f} missing (T3.1 not landed)")
    subprocess.run(["make", "-C", os.path.join(ROOT, "sim")], check=True, capture_output=True)
    import build_fw
    hexf = build_fw.build("mnist_cnn", images=2, runs=1, out=os.path.join(ROOT, "build", "fw", "test_mnist"))["hex"]
    p = subprocess.run([os.path.join(ROOT, "build", "sim", "Vsoc_top"), f"+ram={hexf}", "+max_cycles=400000000"],
                       capture_output=True, text=True, timeout=1200)
    assert p.returncode == 0, p.stdout[-2000:]
    kv = dict(re.findall(r"MCUGRAD_RESULT\s+(\w+)=(\S+)", p.stdout))
    assert "accuracy" in kv and int(kv["cycles_median"]) > 0, kv
