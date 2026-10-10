"""mcuaccel_writer (DESIGN 8.11): byte-to-word assembly, 1-entry pending write, in_ready rule,
random start alignment / tile length / wr_accept delays / input gaps."""
import os
import random
import sys
from pathlib import Path

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.models import mask_data, writer_words  # noqa: E402
from common.runner import ROOT, rtl, run_cocotb  # noqa: E402

RTL = ROOT / "rtl/accel/mcuaccel_writer.sv"
pytestmark = pytest.mark.skipif(not RTL.exists(), reason="RTL not yet written (M2)")
FULL = os.environ.get("MCUACCEL_FULL") == "1"


async def start(dut):
    for s in ("in_valid", "in_last", "wr_accept"):
        getattr(dut, s).value = 0
    dut.in_addr.value = dut.in_byte.value = 0
    dut.rst.value = 1
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)


def make_stream(rng, ntiles, max_nv=17):
    """-> list of tiles, each a list of (addr, byte, last); consecutive addresses within a tile."""
    tiles = []
    for _ in range(ntiles):
        base = rng.randrange(0, 1 << 20) * 4 + rng.randrange(4) + rng.choice((0, 0x8000_0000))
        nv = rng.choice((1, 1, 2, 3, 4, 5, 8, rng.randrange(1, max_nv)))
        tiles.append([(base + i, rng.getrandbits(8), int(i == nv - 1)) for i in range(nv)])
    return tiles


async def run_stream(dut, rng, tiles, in_gap_p, acc_p):
    """Mid-cycle driving: at each FallingEdge read the registered outputs, decide wr_accept and the
    input byte, let combinational outputs settle (1 ns), check, then wait for the clock edge."""
    flat = [b for t in tiles for b in t]
    exp_words = writer_words(flat)
    got_words = []
    pending = None               # expected word currently held in the output register
    asm = []                     # bytes in assembly (accepted, word not complete)
    idx = 0                      # next input byte
    cur_gap = 0
    cyc = 0
    last_ready_cycle = None
    while idx < len(flat) or pending is not None or asm:
        await FallingEdge(dut.clk)
        wv = int(dut.wr_valid.value)
        # registered state must match the model
        assert wv == int(pending is not None), f"cycle {cyc}: wr_valid={wv}, expected {int(pending is not None)}"
        accept = int(wv and rng.random() < acc_p)
        dut.wr_accept.value = accept
        # present the next byte (held until accepted), junk when idle
        present = idx < len(flat) and cur_gap == 0
        if present:
            addr, byte, last = flat[idx]
            dut.in_valid.value, dut.in_addr.value, dut.in_byte.value, dut.in_last.value = 1, addr, byte, last
        else:
            dut.in_valid.value = 0
            dut.in_addr.value, dut.in_byte.value = rng.getrandbits(32), rng.getrandbits(8)
            dut.in_last.value = rng.getrandbits(1)         # junk in_last must not flush
        await Timer(1, unit="ns")
        ready = int(dut.in_ready.value)
        want_ready = int((not wv) or accept)
        assert ready == want_ready, f"cycle {cyc}: in_ready={ready}, expected !wr_valid||wr_accept = {want_ready}"
        idle = int(dut.idle.value)
        if wv:
            waddr, be, data = int(dut.wr_addr.value), int(dut.wr_be.value), int(dut.wr_data.value)
            ea, eb, ed = pending
            assert (waddr, be) == (ea, eb), f"cycle {cyc}: write @{waddr:#x} be {be:04b}, expected @{ea:#x} {eb:04b}"
            assert mask_data(data, be) == mask_data(ed, eb), f"cycle {cyc}: data {data:#010x} vs {ed:#010x} (be {be:04b})"
        if wv or asm:
            assert not idle, f"cycle {cyc}: idle with work in progress"
        if accept:
            got_words.append(pending)
            pending = None
        if present and ready:
            addr, byte, last = flat[idx]
            asm.append((addr, byte, last))
            idx += 1
            lane = addr & 3
            if lane == 3 or last:
                assert pending is None
                pending = exp_words[len(got_words)]
                asm = []
            cur_gap = rng.randrange(1, 4) if rng.random() < in_gap_p else 0
        elif not present and cur_gap > 0:
            cur_gap -= 1
        cyc += 1
        assert cyc < 50 * len(flat) + 1000, "timeout"
        await RisingEdge(dut.clk)
    dut.in_valid.value = 0
    dut.wr_accept.value = 0
    assert len(got_words) == len(exp_words)
    for _ in range(3):
        await RisingEdge(dut.clk)
    await FallingEdge(dut.clk)
    assert int(dut.idle.value) == 1 and int(dut.wr_valid.value) == 0
    await RisingEdge(dut.clk)


@cocotb.test()
async def random_tiles_all_delays(dut):
    await start(dut)
    rng = random.Random(21)
    rounds = 40 if not FULL else 300
    for r in range(rounds):
        tiles = make_stream(rng, rng.randrange(1, 12))
        await run_stream(dut, rng, tiles, rng.choice((0.0, 0.3, 0.8)), rng.choice((1.0, 0.5, 0.15)))


@cocotb.test()
async def directed_alignments(dut):
    """Every start lane x every length 1..8 (word completion on lane 3, flush on in_last)."""
    await start(dut)
    rng = random.Random(4)
    tiles = []
    for lane in range(4):
        for nv in range(1, 9):
            base = 0x2000 + 64 * (len(tiles) % 16) + lane
            tiles.append([(base + i, rng.getrandbits(8), int(i == nv - 1)) for i in range(nv)])
    await run_stream(dut, rng, tiles, 0.0, 1.0)
    await run_stream(dut, rng, tiles, 0.0, 0.3)


def test_mcuaccel_writer():
    run_cocotb("mcuaccel_writer", rtl("accel/mcuaccel_writer.sv"), "test_mcuaccel_writer",
               Path(__file__).parent, "unit_mcuaccel_writer")
