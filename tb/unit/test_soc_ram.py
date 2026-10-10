"""soc_ram: byte enables, range/err, both ports, read-first, 1 transfer/cycle."""
import random
import sys
from pathlib import Path

import cocotb
from cocotb.triggers import ClockCycles

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.bus_checker import BusChecker, port  # noqa: E402
from common.bus_master import BusMaster, clock_reset  # noqa: E402
from common.runner import rtl, run_cocotb  # noqa: E402

BASE, BYTES = 0x8000_0000, 4096


async def setup(dut):
    await clock_reset(dut)
    ms, cs = [], []
    for p in "ab":
        sig = port(dut, p + "_")
        ms.append(BusMaster(dut.clk, sig))
        c = BusChecker(dut.clk, sig, dut.rst, name="ram_" + p)
        c.start()
        cs.append(c)
    return ms, cs


@cocotb.test()
async def byte_enables_and_range(dut):
    (a, b), cs = await setup(dut)
    model = {}
    rng = random.Random(7)
    for i in range(64):  # word writes with random be, both ports
        addr = BASE + 4 * rng.randrange(BYTES // 4)
        data, be = rng.getrandbits(32), rng.getrandbits(4)
        old = model.get(addr, 0)
        for k in range(4):
            if be >> k & 1:
                old = (old & ~(0xFF << 8 * k)) | (data & (0xFF << 8 * k))
        model[addr] = old
        assert (await (a if i % 2 else b).write(addr, data, be)) [1] == 0
    for addr, want in model.items():
        for m in (a, b):
            assert await m.read(addr) == (want, 0), hex(addr)
    # first/last word; out of range gives err and writes nothing
    await a.write(BASE, 0x11223344)
    await a.write(BASE + BYTES - 4, 0x55667788)
    for bad in (BASE - 4, BASE + BYTES, 0, 0xFFFF_FFFC, 0x3000_0000):
        _, err = await b.write(bad, 0xDEADBEEF)
        assert err == 1, hex(bad)
        assert (await a.read(bad))[1] == 1
    assert await a.read(BASE) == (0x11223344, 0)
    assert await b.read(BASE + BYTES - 4) == (0x55667788, 0)
    for c in cs:
        c.check_drained()


@cocotb.test()
async def dual_port_throughput_and_read_first(dut):
    (a, b), cs = await setup(dut)
    # port A streams 16 writes back-to-back while port B streams 16 reads of other words
    await a.run([(1, BASE + 4 * i, 0xA0 + i, 0xF) for i in range(16)])
    t0 = cs[0].cycle
    ra = cocotb.start_soon(a.run([(1, BASE + 4 * i, 0xB00 + i, 0xF) for i in range(16)], True))
    rb = cocotb.start_soon(b.run([(0, BASE + 4 * (32 + i), 0, 0xF) for i in range(16)], True))
    await ra
    await rb
    assert cs[0].cycle - t0 <= 16 + 2, "port A not sustaining 1 transfer/cycle"
    # read-first: B reads a word while A overwrites it in the same cycle
    ra = cocotb.start_soon(a.write(BASE + 0x100, 0x1111_1111))
    rb = cocotb.start_soon(b.write(BASE + 0x104, 0))
    await ra
    await rb
    ra = cocotb.start_soon(a.write(BASE + 0x100, 0x2222_2222))
    rb = cocotb.start_soon(b.read(BASE + 0x100))
    await ra
    assert (await rb) == (0x1111_1111, 0)
    assert await a.read(BASE + 0x100) == (0x2222_2222, 0)
    # idle, then reset clears responses
    await ClockCycles(dut.clk, 3)
    for c in cs:
        c.check_drained()


def test_soc_ram():
    run_cocotb("soc_ram", rtl("soc/soc_ram.sv"), "test_soc_ram", Path(__file__).parent,
               "unit_soc_ram", parameters={"BYTES": BYTES})
