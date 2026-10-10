"""SoC smoke: PicoRV32 runs smoke.c (console, rdcycle, accelerator regs) on soc_top."""
import subprocess
import sys
from pathlib import Path

import cocotb
from cocotb.triggers import FallingEdge

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.bus_checker import BusChecker, port  # noqa: E402
from common.bus_master import clock_reset  # noqa: E402
from common.runner import PICO, ROOT, rtl, run_cocotb  # noqa: E402

BUILD = ROOT / "build" / "soc_smoke"
MAX_CYCLES = 200_000


@cocotb.test()
async def smoke(dut):
    await clock_reset(dut)
    checks = [BusChecker(dut.clk, port(dut, p), dut.rst, name=p + " bus") for p in
              ("c_", "r_", "a_", "k_", "m_")]
    for c in checks:
        c.start()
    out, tohost, cycles = bytearray(), None, 0
    while tohost is None and cycles < MAX_CYCLES:
        await FallingEdge(dut.clk)
        cycles += 1
        if int(dut.putc_valid.value):
            out.append(int(dut.putc_data.value))
        if int(dut.tohost_valid.value):
            tohost = int(dut.tohost_data.value)
        assert not int(dut.cpu_trap.value), f"cpu trap at cycle {cycles}"
        assert not int(dut.cpu_bus_err.value), f"CPU bus error at cycle {cycles}"
    assert tohost is not None, f"no tohost within {MAX_CYCLES} cycles"
    assert tohost == 1, f"firmware failed: code {tohost >> 1}"
    assert bytes(out) == b"OK\n", out
    assert int(dut.accel_busy.value) == 0
    assert checks[2].accepted >= 6, "accelerator registers were not exercised"
    (BUILD / "cycles.txt").write_text(f"{cycles}\n")
    dut._log.info("SMOKE_CYCLES=%d", cycles)


def build_firmware():
    BUILD.mkdir(parents=True, exist_ok=True)
    elf, hexf = BUILD / "smoke.elf", BUILD / "smoke.hex"
    subprocess.run(["riscv-none-elf-gcc", "-march=rv32im", "-mabi=ilp32", "-O2", "-nostdlib",
                    "-Wall", "-Wl,--no-warn-rwx-segments", "-T", str(ROOT / "sw/common/link.ld"),
                    str(ROOT / "sw/common/crt0.S"), str(ROOT / "tb/soc/fw/smoke.c"),
                    "-o", str(elf)], check=True)
    subprocess.run([sys.executable, str(ROOT / "sw/tools/elf2hex.py"), str(elf), str(hexf),
                    "--size", "0x100000"], check=True)
    return hexf


def test_soc_smoke():
    hexf = build_firmware()
    srcs = [PICO] + rtl("soc/soc_top.sv", "soc/soc_picorv32_adapter.sv", "soc/soc_interconnect.sv",
                        "soc/soc_ram.sv", "soc/soc_console.sv", "accel/mcuaccel_top.sv",
                        "accel/mcuaccel_regs.sv")
    run_cocotb("soc_top", srcs, "test_soc_smoke", Path(__file__).parent, "soc_smoke",
               plusargs=[f"+ram={hexf}"])
    print("SMOKE_CYCLES =", (BUILD / "cycles.txt").read_text().strip())
