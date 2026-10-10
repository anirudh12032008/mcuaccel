"""soc_interconnect: address decode, routing, error responder, stalling slaves."""
import random
import sys
from pathlib import Path

import cocotb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.bus_checker import BusChecker, port  # noqa: E402
from common.bus_master import BusMaster, clock_reset  # noqa: E402
from common.ram_slave import RamSlave  # noqa: E402
from common.runner import rtl, run_cocotb  # noqa: E402

RAM_BYTES = 4096
REGIONS = {"r": (0x8000_0000, RAM_BYTES), "a": (0x3000_0000, 0x1000), "k": (0x1000_0000, 0x100)}


async def setup(dut, stall, seed):
    await clock_reset(dut)
    slaves, checks = {}, [BusChecker(dut.clk, port(dut, "c_"), dut.rst, name="cpu")]
    for i, (p, (base, size)) in enumerate(REGIONS.items()):
        sig = port(dut, p + "_")
        slaves[p] = RamSlave(dut.clk, sig, dut.rst, base, size, stall, seed + i)
        slaves[p].start()
        checks.append(BusChecker(dut.clk, sig, dut.rst, name="slave_" + p))
    for c in checks:
        c.start()
    return BusMaster(dut.clk, port(dut, "c_")), slaves, checks


async def decode_and_route(dut, stall, seed):
    m, slaves, checks = await setup(dut, stall, seed)
    rng = random.Random(seed)
    for p, (base, size) in REGIONS.items():  # first and last word of every region
        for addr in (base, base + size - 4, base + 4 * rng.randrange(size // 4)):
            v = rng.getrandbits(32)
            assert (await m.write(addr, v))[1] == 0
            assert await m.read(addr) == (v, 0), (p, hex(addr))
            assert slaves[p].word(addr) == v
    before = {p: (s.reads, s.writes) for p, s in slaves.items()}
    bad = [0, 0x0FFF_FFFC, 0x1000_0100, 0x1000_0FFC, 0x2FFF_FFFC, 0x3000_1000, 0x3000_FFFC,
           0x7FFF_FFFC, 0x8000_0000 + RAM_BYTES, 0xFFFF_FFFC]
    for addr in bad:
        for we in (0, 1):
            rdata, err = await m.access(we, addr, 0x1234_5678)
            assert err == 1 and rdata == 0, (hex(addr), we, rdata, err)
    assert before == {p: (s.reads, s.writes) for p, s in slaves.items()}, "error access leaked"
    # mixed random traffic, compare with a flat model
    model = {}
    for _ in range(100):
        p = rng.choice(list(REGIONS))
        base, size = REGIONS[p]
        addr = base + 4 * rng.randrange(size // 4)
        if rng.random() < 0.5:
            v, be = rng.getrandbits(32), rng.getrandbits(4)
            old = model.setdefault(addr, slaves[p].word(addr))
            for k in range(4):
                if be >> k & 1:
                    old = (old & ~(0xFF << 8 * k)) | (v & (0xFF << 8 * k))
            model[addr] = old
            assert (await m.write(addr, v, be))[1] == 0
        else:
            assert await m.read(addr) == (model.setdefault(addr, slaves[p].word(addr)), 0)
    for c in checks:
        c.check_drained()


@cocotb.test()
async def decode_zero_wait(dut):
    await decode_and_route(dut, False, 1)


@cocotb.test()
async def decode_stalling(dut):
    await decode_and_route(dut, True, 5)


def test_soc_interconnect():
    run_cocotb("soc_interconnect", rtl("soc/soc_interconnect.sv"), "test_soc_interconnect",
               Path(__file__).parent, "unit_soc_interconnect",
               parameters={"RAM_BYTES": RAM_BYTES})
