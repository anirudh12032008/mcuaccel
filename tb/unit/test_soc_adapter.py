"""soc_picorv32_adapter driven by a PicoRV32-like native-interface model."""
import random
import sys
from pathlib import Path

import cocotb
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.bus_checker import BusChecker, port  # noqa: E402
from common.bus_master import clock_reset  # noqa: E402
from common.ram_slave import RamSlave  # noqa: E402
from common.runner import rtl, run_cocotb  # noqa: E402

BASE, SIZE = 0x8000_0000, 1024


class PicoDriver:
    """Raises mem_valid, holds addr/wdata/wstrb until mem_ready, then drops it for >= 1 cycle
    (PicoRV32 never re-raises mem_valid in the cycle right after mem_ready)."""

    def __init__(self, dut):
        self.dut = dut
        dut.mem_valid.value = 0
        dut.mem_instr.value = 0
        for f in (dut.mem_addr, dut.mem_wdata, dut.mem_wstrb):
            f.value = 0

    async def xfer(self, addr, wdata=0, wstrb=0, gap=0):
        d = self.dut
        d.mem_valid.value, d.mem_addr.value = 1, addr
        d.mem_wdata.value, d.mem_wstrb.value = wdata, wstrb
        n = 0
        while True:
            await FallingEdge(d.clk)
            n += 1
            if int(d.mem_ready.value):
                rdata = int(d.mem_rdata.value)
                break
            assert n < 100, "mem_ready never came"
        await RisingEdge(d.clk)
        d.mem_valid.value = 0
        d.mem_wdata.value = 0xA5A5A5A5  # PicoRV32 may change these once mem_valid is low
        for _ in range(gap):
            await RisingEdge(d.clk)
        return rdata, n


async def run(dut, stall, seed):
    await clock_reset(dut)
    sig = port(dut, "m_")
    slave = RamSlave(dut.clk, sig, dut.rst, BASE, SIZE, stall, seed)
    slave.start()
    chk = BusChecker(dut.clk, sig, dut.rst, name="adapter")
    chk.start()
    cpu = PicoDriver(dut)
    rng, model = random.Random(seed), {}
    for _ in range(150):
        addr = BASE + 4 * rng.randrange(SIZE // 4) + rng.randrange(4)  # byte offset is dropped
        w = addr & ~3
        gap = rng.choice([0, 0, 1, 3])
        if rng.random() < 0.5:
            wstrb = rng.randrange(1, 16)  # sb/sh/sw patterns and odd ones
            v = rng.getrandbits(32)
            old = model.get(w, 0)
            for k in range(4):
                if wstrb >> k & 1:
                    old = (old & ~(0xFF << 8 * k)) | (v & (0xFF << 8 * k))
            model[w] = old
            await cpu.xfer(addr, v, wstrb, gap)
        else:
            rdata, n = await cpu.xfer(addr, rng.getrandbits(32), 0, gap)
            assert rdata == model.get(w, 0), hex(addr)
            if not stall:
                assert n == 2, f"zero-wait read should take 2 sampled cycles, took {n}"
    assert int(dut.bus_err.value) == 0
    # out of range: err response sets sticky bus_err, and stays set
    await cpu.xfer(BASE + SIZE, 0, 0)
    await ClockCycles(dut.clk, 2)
    assert int(dut.bus_err.value) == 1
    await cpu.xfer(BASE, 0, 0)
    assert int(dut.bus_err.value) == 1
    chk.check_drained()
    dut.rst.value = 1
    await ClockCycles(dut.clk, 2)
    await FallingEdge(dut.clk)
    assert int(dut.bus_err.value) == 0 and int(dut.m_req.value) == 0


@cocotb.test()
async def adapter_zero_wait(dut):
    await run(dut, False, 3)


@cocotb.test()
async def adapter_stalling(dut):
    await run(dut, True, 11)


def test_soc_adapter():
    run_cocotb("soc_picorv32_adapter", rtl("soc/soc_picorv32_adapter.sv"), "test_soc_adapter",
               Path(__file__).parent, "unit_soc_adapter")
