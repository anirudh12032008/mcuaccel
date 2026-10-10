"""mcuaccel_fetch (DESIGN 8.4): the accelerator's only bus master. Read/write mixes against a
zero-wait and a stalling slave (tb/common/ram_slave.py) with bus_checker on m_*; tags, addresses and
we returned correctly; write priority; hold rule; one request per cycle when the slave is zero-wait."""
import random
import sys
from collections import deque
from pathlib import Path

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.bus_checker import BusChecker, port  # noqa: E402
from common.ram_slave import RamSlave  # noqa: E402
from common.runner import ROOT, rtl, run_cocotb  # noqa: E402

RTL = ROOT / "rtl/accel/mcuaccel_fetch.sv"
pytestmark = pytest.mark.skipif(not RTL.exists(), reason="RTL not yet written (M2)")

BASE, SIZE = 0x8000_0000, 4096
RD_END = BASE + 2048          # reads below, writes at/above (disjoint, so read data is static)


async def setup(dut, stall, seed):
    for s in ("rd_valid", "wr_valid"):
        getattr(dut, s).value = 0
    dut.rd_addr.value = dut.rd_tag.value = dut.wr_addr.value = dut.wr_be.value = dut.wr_data.value = 0
    dut.rst.value = 1
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    sig = port(dut, "m_")
    slave = RamSlave(dut.clk, sig, dut.rst, base=BASE, size=SIZE, stall=stall, seed=seed)
    rng = random.Random(seed)
    for i in range(SIZE):
        slave.mem[i] = rng.getrandbits(8)
    init = bytes(slave.mem)
    slave.start()
    chk = BusChecker(dut.clk, sig, dut.rst, name="fetch_m")
    chk.start()
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)
    return slave, chk, init


def make_ops(rng, n):
    """-> (rd_ops [(addr, tag)], wr_ops [(addr, be, data)]) including out-of-range accesses."""
    rd, wr = [], []
    for _ in range(n):
        if rng.random() < .5:
            a = BASE + 4 * rng.randrange(512)
            if rng.random() < .06:
                a = rng.choice((BASE + SIZE, BASE + SIZE + 4 * rng.randrange(8), 0, 0x3000_0000, BASE - 4))
            rd.append((a, rng.getrandbits(20)))
        else:
            a = RD_END + 4 * rng.randrange(512)
            if rng.random() < .06:
                a = rng.choice((BASE + SIZE, 0x1000_0000, BASE - 4))
            wr.append((a, rng.choice((0xF, 0xF, rng.getrandbits(4))), rng.getrandbits(32)))
    return rd, wr


async def drive(dut, slave, init, rd_ops, wr_ops, rd_density, wr_density, rng, expect_zero_wait=False):
    rd_ops, wr_ops = deque(rd_ops), deque(wr_ops)
    n_total = len(rd_ops) + len(wr_ops)
    model_mem = bytearray(init)
    cur_rd = cur_wr = None
    rd_gap = wr_gap = 0
    inflight = deque()
    done = cyc = 0
    prev_unacc = False          # m_req && !m_gnt in the previous cycle
    prev_unacc_read = False
    stats = dict(held_read_then_write=0, writes_first=0, accepts=[], resp=0)
    while done < n_total:
        # ---- drive (right after RisingEdge); requests are held until accepted
        if cur_rd is None and rd_ops:
            if rd_gap > 0:
                rd_gap -= 1
            else:
                cur_rd = rd_ops.popleft()
        if cur_wr is None and wr_ops:
            if wr_gap > 0:
                wr_gap -= 1
            else:
                cur_wr = wr_ops.popleft()
        if cur_rd is not None:
            dut.rd_valid.value, dut.rd_addr.value, dut.rd_tag.value = 1, cur_rd[0], cur_rd[1]
        else:
            dut.rd_valid.value = 0
            dut.rd_addr.value, dut.rd_tag.value = rng.getrandbits(32) & ~3, rng.getrandbits(20)
        if cur_wr is not None:
            dut.wr_valid.value, dut.wr_addr.value = 1, cur_wr[0]
            dut.wr_be.value, dut.wr_data.value = cur_wr[1], cur_wr[2]
        else:
            dut.wr_valid.value = 0
            dut.wr_addr.value, dut.wr_be.value, dut.wr_data.value = rng.getrandbits(32) & ~3, rng.getrandbits(4), rng.getrandbits(32)
        await FallingEdge(dut.clk)
        rv, wv = int(dut.rd_valid.value), int(dut.wr_valid.value)
        ra, wa = int(dut.rd_accept.value), int(dut.wr_accept.value)
        m_req, m_gnt, m_we = int(dut.m_req.value), int(dut.m_gnt.value), int(dut.m_we.value)
        # ---- responses first (the previous request), then this cycle's acceptance
        if int(dut.resp_valid.value):
            assert inflight, f"cycle {cyc}: resp_valid with nothing outstanding"
            kind, addr, tag = inflight.popleft()
            assert int(dut.resp_we.value) == (kind == "W"), f"cycle {cyc}: resp_we"
            assert int(dut.resp_addr.value) == addr, f"cycle {cyc}: resp_addr {int(dut.resp_addr.value):#x} != {addr:#x}"
            inrange = BASE <= addr < BASE + SIZE
            assert int(dut.resp_err.value) == int(not inrange), f"cycle {cyc}: resp_err for {addr:#x}"
            if kind == "R":
                assert int(dut.resp_tag.value) == tag, f"cycle {cyc}: resp_tag {int(dut.resp_tag.value):#x} != {tag:#x}"
                if inrange:
                    o = addr - BASE
                    assert int(dut.resp_rdata.value) == int.from_bytes(init[o:o + 4], "little"), f"cycle {cyc}: resp_rdata"
            done += 1
            stats["resp"] += 1
        waiting = len(inflight) > 0          # accepted earlier, response still to come
        # ---- acceptance
        assert not (ra and wa), f"cycle {cyc}: both accepts"
        assert not ra or rv, f"cycle {cyc}: rd_accept without rd_valid"
        assert not wa or wv, f"cycle {cyc}: wr_accept without wr_valid"
        # request selection: a new request (not held from the previous cycle) obeys write priority
        if m_req:
            assert rv or wv, f"cycle {cyc}: m_req with no requester valid"
            if not prev_unacc and wv:
                assert m_we == 1, f"cycle {cyc}: write pending but a read was raised (write priority)"
                stats["writes_first"] += 1
            if m_we:
                assert wv and int(dut.m_addr.value) == int(dut.wr_addr.value), f"cycle {cyc}: m_addr != wr_addr"
                assert int(dut.m_be.value) == int(dut.wr_be.value) and int(dut.m_wdata.value) == int(dut.wr_data.value)
            else:
                assert rv and int(dut.m_addr.value) == int(dut.rd_addr.value), f"cycle {cyc}: m_addr != rd_addr"
            if prev_unacc_read and wv and not m_we:
                stats["held_read_then_write"] += 1
        else:
            assert not (ra or wa), f"cycle {cyc}: accept without m_req"
        if m_req and m_gnt:
            assert (ra or wa) and (ra == (not m_we)), f"cycle {cyc}: accept does not match m_req&&m_gnt"
        else:
            assert not (ra or wa), f"cycle {cyc}: accept without grant"
        if ra:
            inflight.append(("R", cur_rd[0], cur_rd[1]))
            cur_rd, rd_gap = None, (rng.randrange(0, 4) if rng.random() > rd_density else 0)
            stats["accepts"].append(cyc)
        if wa:
            a, be, d = cur_wr
            inflight.append(("W", a, 0))
            if BASE <= a < BASE + SIZE:
                o = a - BASE
                for k in range(4):
                    if be >> k & 1:
                        model_mem[o + k] = d >> (8 * k) & 0xFF
            cur_wr, wr_gap = None, (rng.randrange(0, 4) if rng.random() > wr_density else 0)
            stats["accepts"].append(cyc)
        # idle: never while an earlier request still awaits its response, nor while a request has
        # been held for a cycle (the acceptance and response cycles themselves are not checked)
        if waiting and int(dut.idle.value):
            raise AssertionError(f"cycle {cyc}: idle while a request is outstanding")
        if prev_unacc and m_req and int(dut.idle.value):
            raise AssertionError(f"cycle {cyc}: idle while a request is held")
        prev_unacc = bool(m_req and not m_gnt)
        prev_unacc_read = prev_unacc and not m_we
        cyc += 1
        assert cyc < 40 * n_total + 500, "timeout: responses missing"
        await RisingEdge(dut.clk)
    dut.rd_valid.value = dut.wr_valid.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    await FallingEdge(dut.clk)
    assert int(dut.idle.value) == 1, "idle did not return after the last response"
    assert int(dut.m_req.value) == 0 and not inflight
    assert bytes(slave.mem) == bytes(model_mem), "slave memory differs from the expected write effects"
    await RisingEdge(dut.clk)
    return stats


@cocotb.test()
async def mixed_traffic_zero_wait(dut):
    slave, chk, init = await setup(dut, False, 1)
    rng = random.Random(2)
    rd, wr = make_ops(rng, 600)
    st = await drive(dut, slave, init, rd, wr, 0.9, 0.9, rng)
    chk.check_drained()
    assert st["writes_first"] > 0


@cocotb.test()
async def mixed_traffic_stalling_slave(dut):
    slave, chk, init = await setup(dut, True, 3)
    rng = random.Random(4)
    total = dict(held_read_then_write=0, writes_first=0)
    for dens in ((0.9, 0.9), (0.5, 0.5), (1.0, 0.1), (0.1, 1.0)):
        rd, wr = make_ops(rng, 500)
        init = bytes(slave.mem)
        # the read region is never written, the write region is re-snapshotted per round
        st = await drive(dut, slave, init, rd, wr, dens[0], dens[1], rng)
        for k in total:
            total[k] += st[k]
    chk.check_drained()
    assert total["writes_first"] > 0 and total["held_read_then_write"] > 0, total


@cocotb.test()
async def one_transfer_per_cycle_and_priority(dut):
    """Zero-wait slave, requesters always valid: one accept per cycle (rule 3 allows raising a
    request in the response cycle). From idle with both valid, the write is accepted first."""
    slave, chk, init = await setup(dut, False, 5)
    rng = random.Random(6)
    n = 64
    rd = [(BASE + 4 * rng.randrange(512), i) for i in range(n)]
    st = await drive(dut, slave, init, rd, [], 1.0, 1.0, rng)
    acc = st["accepts"]
    assert acc[-1] - acc[0] <= n, f"reads not back to back: first {acc[0]} last {acc[-1]} for {n}"
    wr = [(RD_END + 4 * i, 0xF, rng.getrandbits(32)) for i in range(n)]
    st = await drive(dut, slave, bytes(slave.mem), [], wr, 1.0, 1.0, rng)
    assert st["accepts"][-1] - st["accepts"][0] <= n
    # priority from idle: one read and one write valid in the same cycle
    await FallingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.rd_valid.value, dut.rd_addr.value, dut.rd_tag.value = 1, BASE, 0x12345
    dut.wr_valid.value, dut.wr_addr.value, dut.wr_be.value, dut.wr_data.value = 1, RD_END, 0xF, 0xCAFEF00D
    order = []
    for _ in range(12):
        await FallingEdge(dut.clk)
        wa, ra = int(dut.wr_accept.value), int(dut.rd_accept.value)
        await RisingEdge(dut.clk)
        if wa:
            order.append("W")
            dut.wr_valid.value = 0
        if ra:
            order.append("R")
            dut.rd_valid.value = 0
    assert order == ["W", "R"], order
    await RisingEdge(dut.clk)
    chk.check_drained()


def test_mcuaccel_fetch():
    run_cocotb("mcuaccel_fetch", rtl("accel/mcuaccel_fetch.sv"), "test_mcuaccel_fetch",
               Path(__file__).parent, "unit_mcuaccel_fetch")
