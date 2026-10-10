"""mcuaccel_pbuf (DESIGN 8.7): three 512-deep memories (bias 32 b, mult 32 b, shift 6 b), write
selected by wsel (0 bias, 1 mult, 2 shift), synchronous read of all three, output holds
when !re."""
import os
import random
import sys
from pathlib import Path

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.runner import ROOT, rtl, run_cocotb  # noqa: E402

RTL = ROOT / "rtl/accel/mcuaccel_pbuf.sv"
pytestmark = pytest.mark.skipif(not RTL.exists(), reason="RTL not yet written (M2)")
FULL = os.environ.get("MCUACCEL_FULL") == "1"
WIDTH = (32, 32, 6)


class Bench:
    def __init__(self, dut):
        self.dut = dut
        self.mem = {}                  # (ch, sel) -> value
        self.out = None                # expected (bias, mult, shift) entries, None = unknown
        self.cyc = 0
        for s in ("we", "re"):
            getattr(dut, s).value = 0
        dut.waddr.value = dut.wsel.value = dut.wdata.value = dut.raddr.value = 0

    async def start(self):
        cocotb.start_soon(Clock(self.dut.clk, 10, unit="ns").start())
        await RisingEdge(self.dut.clk)

    async def tick(self, we=0, waddr=0, wsel=0, wdata=0, re=0, raddr=0):
        d = self.dut
        assert not (we and re and waddr == raddr), "test bug: same-channel read/write in one cycle"
        d.we.value, d.waddr.value, d.wsel.value, d.wdata.value = we, waddr, wsel, wdata
        d.re.value, d.raddr.value = re, raddr
        await FallingEdge(d.clk)
        if self.out is not None:
            got = (int(d.bias.value), int(d.mult.value), int(d.shift.value))
            for k, (g, e) in enumerate(zip(got, self.out)):
                assert e is None or g == e, f"cycle {self.cyc}: {('bias', 'mult', 'shift')[k]} = {g:#x}, expected {e:#x}"
        if re:
            self.out = [self.mem.get((raddr, k)) for k in range(3)]
        if we:
            self.mem[(waddr, wsel)] = wdata & ((1 << WIDTH[wsel]) - 1)     # shift keeps bits [5:0]
        self.cyc += 1
        await RisingEdge(d.clk)


@cocotb.test()
async def write_then_read_and_hold(dut):
    b = Bench(dut)
    await b.start()
    rng = random.Random(15)
    chans = [0, 1, 2, 3, 254, 255, 256, 257, 509, 510, 511] + [rng.randrange(512) for _ in range(20)]
    for c in chans:
        for sel in range(3):
            await b.tick(we=1, waddr=c, wsel=sel, wdata=rng.getrandbits(32))   # shift: upper bits dropped
    for c in chans:
        await b.tick(re=1, raddr=c)
        await b.tick()
        await b.tick(we=1, waddr=c ^ 1, wsel=rng.randrange(3), wdata=rng.getrandbits(32), raddr=c)
    for _ in range(100):
        c, sel = rng.choice(chans), rng.randrange(3)
        await b.tick(we=1, waddr=c, wsel=sel, wdata=rng.getrandbits(32))
        await b.tick(re=1, raddr=c)
    for _ in range(4000 if not FULL else 40000):
        we, re = int(rng.random() < .5), int(rng.random() < .5)
        waddr, raddr = rng.choice(chans), rng.choice(chans)
        if we and re and waddr == raddr:
            re = 0
        wsel = rng.randrange(3 if we else 4)          # wsel 3 is never written (unspecified)
        await b.tick(we, waddr, wsel, rng.getrandbits(32), re, raddr)
    assert b.out is not None


def test_mcuaccel_pbuf():
    run_cocotb("mcuaccel_pbuf", rtl("accel/mcuaccel_pbuf.sv"), "test_mcuaccel_pbuf",
               Path(__file__).parent, "unit_mcuaccel_pbuf")
