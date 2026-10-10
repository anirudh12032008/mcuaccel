"""Toolchain smoke tests: Verilator+cocotb, riscv gcc, yosys synth_ecp5."""
import subprocess
from pathlib import Path

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge
from cocotb_tools.check_results import get_results
from cocotb_tools.runner import get_runner

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build" / "unit_toolchain"
SRC = ROOT / "rtl/soc/soc_smoke_counter.sv"


@cocotb.test()
async def counter_counts(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    await ClockCycles(dut.clk, 2)
    dut.rst.value = 0
    await RisingEdge(dut.clk)
    prev = int(dut.q.value)
    for _ in range(300):  # crosses the 8-bit wrap
        await RisingEdge(dut.clk)
        cur = int(dut.q.value)
        assert cur == (prev + 1) & 0xFF, (prev, cur)
        prev = cur


def test_cocotb_verilator_counter():
    runner = get_runner("verilator")
    runner.build(sources=[SRC], hdl_toplevel="soc_smoke_counter", build_dir=BUILD / "sim",
                 build_args=["-Wno-fatal", "--timing"], always=True)
    xml = runner.test(hdl_toplevel="soc_smoke_counter", test_module="test_toolchain",
                      test_dir=Path(__file__).parent, build_dir=BUILD / "sim",
                      results_xml=str(BUILD / "results.xml"))
    total, failed = get_results(Path(xml))
    assert total == 1 and failed == 0, f"cocotb: {failed}/{total} failed"


@pytest.mark.needs_tools
def test_riscv_gcc_compiles(tmp_path):
    c = tmp_path / "t.c"
    c.write_text("int f(int a, int b) {\n  int s = 0;\n  for (int i = 0; i < b; i++) s += a * i;\n"
                 "  return s;\n}\n")
    o = tmp_path / "t.o"
    subprocess.run(["riscv-none-elf-gcc", "-march=rv32im", "-mabi=ilp32", "-nostdlib", "-c",
                    str(c), "-o", str(o)], check=True)
    assert o.exists() and o.stat().st_size > 0


@pytest.mark.needs_tools
def test_yosys_synth_ecp5():
    subprocess.run(["yosys", "-q", "-p",
                    f"read_verilog -sv {SRC}; synth_ecp5 -top soc_smoke_counter"],
                   check=True, cwd=ROOT)
