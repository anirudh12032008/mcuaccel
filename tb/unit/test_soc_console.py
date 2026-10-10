"""soc_console: PUTCHAR / TOHOST pulses, cycle counter, ignored accesses."""
import sys
from pathlib import Path

import cocotb
from cocotb.triggers import ClockCycles, FallingEdge

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.bus_checker import BusChecker, port  # noqa: E402
from common.bus_master import BusMaster, clock_reset  # noqa: E402
from common.runner import rtl, run_cocotb  # noqa: E402

K = 0x1000_0000


class Monitor:
    """Records (putc bytes, tohost words) seen at falling edges."""

    def __init__(self, dut):
        self.putc, self.tohost = [], []
        cocotb.start_soon(self._run(dut))

    async def _run(self, dut):
        while True:
            await FallingEdge(dut.clk)
            if int(dut.putc_valid.value):
                self.putc.append(int(dut.putc_data.value))
            if int(dut.tohost_valid.value):
                self.tohost.append(int(dut.tohost_data.value))


@cocotb.test()
async def console_regs(dut):
    await clock_reset(dut)
    sig = port(dut, "s_")
    m, mon = BusMaster(dut.clk, sig), Monitor(dut)
    chk = BusChecker(dut.clk, sig, dut.rst, name="console")
    chk.start()
    for ch in b"OK\n":
        assert (await m.write(K, ch, 0x1))[1] == 0
    assert mon.putc == list(b"OK\n")
    await m.write(K, 0x41, 0x0)         # no byte enable: ignored
    await m.write(K, 0x42, 0x2)         # lane 1 only: ignored
    await m.write(K + 0x10, 0x43, 0xF)  # outside the defined registers: ignored
    await m.write(K + 0xFC, 0x44, 0xF)
    assert mon.putc == list(b"OK\n")
    await m.write(K + 4, 0x8000_0003, 0xF)
    await m.write(K + 4, 0x1, 0x3)      # partial: ignored
    assert mon.tohost == [0x8000_0003]
    # pulses are single-cycle
    await ClockCycles(dut.clk, 3)
    assert len(mon.putc) == 3 and len(mon.tohost) == 1
    # cycle counter: monotone, low word advances by the transfer period, high word 0
    lo = [(await m.read(K + 8))[0] for _ in range(4)]
    assert all(0 < b - a < 16 for a, b in zip(lo, lo[1:])), lo
    assert (await m.read(K + 0xC))[0] == 0
    for off in (0x10, 0x14, 0xFC):
        assert await m.read(K + off) == (0, 0)
    await m.write(K + 8, 0xFFFF_FFFF)  # read-only: ignored
    assert 0 < (await m.read(K + 8))[0] - lo[-1] < 16
    chk.check_drained()


def test_soc_console():
    run_cocotb("soc_console", rtl("soc/soc_console.sv"), "test_soc_console", Path(__file__).parent,
               "unit_soc_console")
