"""mcuaccel_wbuf (DESIGN 8.6): P x (512 x 32) memories, write word `wword` of row `waddr`,
synchronous read of all P words, output holds when !re."""
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

RTL = ROOT / "rtl/accel/mcuaccel_wbuf.sv"
pytestmark = pytest.mark.skipif(not RTL.exists(), reason="RTL not yet written (M2)")
FULL = os.environ.get("MCUACCEL_FULL") == "1"
PS = [8, 1, 4, 16] if FULL else [8, 1]


class Bench:
    def __init__(self, dut, P):
        self.dut, self.P = dut, P
        self.mem = {}                  # (row, word) -> value
        self.out = None                # expected rdata words (None entries = unknown); None = never read
        self.cyc = 0
        for s in ("we", "re"):
            getattr(dut, s).value = 0
        dut.waddr.value = dut.wword.value = dut.wdata.value = dut.raddr.value = 0

    async def start(self):
        cocotb.start_soon(Clock(self.dut.clk, 10, unit="ns").start())
        await RisingEdge(self.dut.clk)

    async def tick(self, we=0, waddr=0, wword=0, wdata=0, re=0, raddr=0, ):
        d, P = self.dut, self.P
        assert not (we and re and waddr == raddr), "test bug: same-row read/write in one cycle"
        d.we.value, d.waddr.value, d.wword.value, d.wdata.value = we, waddr, wword, wdata
        d.re.value, d.raddr.value = re, raddr
        await FallingEdge(d.clk)
        if self.out is not None:
            got = int(d.rdata.value)
            for p in range(P):
                e = self.out[p]
                if e is not None:
                    g = got >> (32 * p) & 0xFFFF_FFFF
                    assert g == e, f"cycle {self.cyc}: rdata word {p} = {g:#010x}, expected {e:#010x}"
        # model: read first (old contents), then the write lands at the clock edge
        if re:
            self.out = [self.mem.get((raddr, p)) for p in range(P)]
        if we and wword < P:
            self.mem[(waddr, wword)] = wdata
        self.cyc += 1
        await RisingEdge(d.clk)


@cocotb.test()
async def write_then_read_and_hold(dut):
    P = int(os.environ["MCUACCEL_P"])
    b = Bench(dut, P)
    await b.start()
    rng = random.Random(8 + P)
    rows = [0, 1, 2, 3, 255, 256, 257, 508, 509, 510, 511] + [rng.randrange(512) for _ in range(20)]
    for r in rows:                                  # fill every word of the chosen rows
        for p in range(P):
            await b.tick(we=1, waddr=r, wword=p, wdata=rng.getrandbits(32))
    for r in rows:                                  # read each; then idle cycles: output holds
        await b.tick(re=1, raddr=r)
        await b.tick()
        await b.tick(we=1, waddr=(r + 1) % 512, wword=0, wdata=rng.getrandbits(32), re=0, raddr=r ^ 1)
    # write at t, read the same row at t+1 sees the new data
    for _ in range(100):
        r, p = rng.choice(rows), rng.randrange(P)
        await b.tick(we=1, waddr=r, wword=p, wdata=rng.getrandbits(32))
        await b.tick(re=1, raddr=r)
    # words beyond P are ignored (P < 16); random mixed traffic with junk addresses on idle ports
    for _ in range(4000 if not FULL else 40000):
        we, re = int(rng.random() < .5), int(rng.random() < .5)
        waddr, raddr = rng.choice(rows), rng.choice(rows)
        if we and re and waddr == raddr:
            re = 0
        wword = rng.randrange(16)
        await b.tick(we, waddr, wword, rng.getrandbits(32), re, raddr)
    assert b.out is not None


@pytest.mark.parametrize("p", PS)
def test_mcuaccel_wbuf_p(p, monkeypatch):
    monkeypatch.setenv("MCUACCEL_P", str(p))
    run_cocotb("mcuaccel_wbuf", rtl("accel/mcuaccel_wbuf.sv"), "test_mcuaccel_wbuf",
               Path(__file__).parent, f"unit_mcuaccel_wbuf_p{p}", parameters={"P": p})
