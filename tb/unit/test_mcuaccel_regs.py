"""mcuaccel_regs: register map of DESIGN 6.1, START/CLEAR/DONE/ERR semantics, counters."""
import sys
from pathlib import Path

import cocotb
from cocotb.triggers import ClockCycles, FallingEdge

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.bus_checker import BusChecker, port  # noqa: E402
from common.bus_master import BusMaster, clock_reset  # noqa: E402
from common.runner import rtl, run_cocotb  # noqa: E402

A = 0x3000_0000
ID, CONFIG, CTRL, STATUS, DESC, CYCLES, NCMD, ERR_ADDR = (A + 4 * i for i in range(8))
BUSY, DONE, ERR = 1, 2, 4


class Starts:
    """Counts cycles in which `start` is high."""

    def __init__(self, dut):
        self.n = 0
        cocotb.start_soon(self._run(dut))

    async def _run(self, dut):
        while True:
            await FallingEdge(dut.clk)
            self.n += int(dut.start.value)


async def setup(dut):
    for s in ("busy", "cmd_done", "chain_done", "err"):
        getattr(dut, s).value = 0
    dut.err_addr.value = 0
    await clock_reset(dut)
    sig = port(dut, "s_")
    chk = BusChecker(dut.clk, sig, dut.rst, name="regs")
    chk.start()
    return BusMaster(dut.clk, sig), Starts(dut), chk


async def pulse(dut, name, cycles=1):
    getattr(dut, name).value = 1
    await ClockCycles(dut.clk, cycles)
    getattr(dut, name).value = 0


@cocotb.test()
async def reads_and_desc_addr(dut):
    m, st, chk = await setup(dut)
    assert await m.read(ID) == (0x4D43_4131, 0)
    assert await m.read(CONFIG) == ((512 << 16) | (4 << 8) | 8, 0)
    assert (await m.read(STATUS))[0] == 0
    assert (await m.read(CTRL))[0] == 0  # write-only
    for off in (0x20, 0x24, 0x100, 0xFFC):
        assert await m.read(A + off) == (0, 0)
    for r in (CYCLES, NCMD, ERR_ADDR, DESC):
        assert (await m.read(r))[0] == 0
    await m.write(DESC, 0x8000_1234)
    assert (await m.read(DESC))[0] == 0x8000_1234
    await m.write(DESC, 0x1111_1111, be=0x7)  # partial write ignored
    await m.write(A + 0x30, 0x2222_2222)      # unlisted offset ignored
    assert (await m.read(DESC))[0] == 0x8000_1234
    for r in (ID, CONFIG, STATUS, CYCLES, NCMD, ERR_ADDR):  # RO registers ignore writes
        await m.write(r, 0xFFFF_FFFF)
    assert await m.read(ID) == (0x4D43_4131, 0)
    assert (await m.read(STATUS))[0] == 0 and st.n == 0
    chk.check_drained()


@cocotb.test()
async def start_done_clear_counters(dut):
    m, st, chk = await setup(dut)
    # START with partial be or bit0 clear: no pulse
    await m.write(CTRL, 1, be=0x3)
    await m.write(CTRL, 2)
    await m.write(CTRL, 0)
    assert st.n == 0
    # START while busy: ignored
    dut.busy.value = 1
    await m.write(CTRL, 1)
    assert st.n == 0
    dut.busy.value = 0
    await m.write(CTRL, 1)
    assert st.n == 1  # exactly one pulse
    # emulate a 5-cycle chain with 2 commands and an earlier-stale count
    dut.busy.value = 1
    await ClockCycles(dut.clk, 2)
    await pulse(dut, "cmd_done")
    await ClockCycles(dut.clk, 1)
    dut.busy.value = 0
    await pulse(dut, "cmd_done")
    await pulse(dut, "chain_done")
    s = (await m.read(STATUS))[0]
    assert s == DONE, s
    cyc, ncmd = (await m.read(CYCLES))[0], (await m.read(NCMD))[0]
    assert cyc == 4 and ncmd == 2, (cyc, ncmd)  # busy high for 4 clock edges
    assert (await m.read(STATUS))[0] == DONE  # sticky
    await m.write(CTRL, 2)  # CLEAR
    assert (await m.read(STATUS))[0] == 0
    # error path: ERR sticky and ERR_ADDR kept; START clears ERR, DONE and counters
    dut.err_addr.value = 0xDEAD_BEE0
    await pulse(dut, "err")
    dut.err_addr.value = 0
    await pulse(dut, "chain_done")
    assert (await m.read(STATUS))[0] == ERR | DONE
    assert (await m.read(ERR_ADDR))[0] == 0xDEAD_BEE0
    await m.write(CTRL, 1)
    assert st.n == 2
    assert (await m.read(STATUS))[0] == 0
    assert (await m.read(CYCLES))[0] == 0 and (await m.read(NCMD))[0] == 0
    assert (await m.read(ERR_ADDR))[0] == 0  # held until the next START or CLEAR (DESIGN 6.1)
    dut.err_addr.value = 0xDEAD_BEE4
    await pulse(dut, "err")
    assert (await m.read(ERR_ADDR))[0] == 0xDEAD_BEE4
    await m.write(CTRL, 2)  # CLEAR
    assert (await m.read(ERR_ADDR))[0] == 0
    # START + CLEAR in one write behaves like START
    await m.write(CTRL, 3)
    assert st.n == 3
    chk.check_drained()


def test_mcuaccel_regs():
    run_cocotb("mcuaccel_regs", rtl("accel/mcuaccel_regs.sv"), "test_mcuaccel_regs",
               Path(__file__).parent, "unit_mcuaccel_regs")
