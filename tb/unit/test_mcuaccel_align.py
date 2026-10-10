"""mcuaccel_align (DESIGN 8.8): all 4 alignments, random words, prev loads only on in_valid."""
import random
import sys
from pathlib import Path

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.models import AlignModel  # noqa: E402
from common.runner import ROOT, rtl, run_cocotb  # noqa: E402

RTL = ROOT / "rtl/accel/mcuaccel_align.sv"
pytestmark = pytest.mark.skipif(not RTL.exists(), reason="RTL not yet written (M2)")


async def start(dut):
    dut.in_valid.value = 0
    dut.in_word.value = 0
    dut.in_a.value = 0
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await RisingEdge(dut.clk)


@cocotb.test()
async def random_stream_all_alignments(dut):
    """Every cycle: random valid / word / alignment (a changes freely). quad is combinational
    in (prev, in_word, in_a); checked on valid cycles. Invalid cycles carry garbage words that
    must not reach `prev`."""
    await start(dut)
    rng = random.Random(11)
    model = AlignModel()
    seen = [0] * 4
    for cyc in range(6000):
        first = cyc == 0                       # prev is not reset: load it with an a = 0 word
        valid = 1 if first else int(rng.random() < 0.7)
        word = rng.getrandbits(32)
        a = 0 if first else rng.randrange(4)
        dut.in_valid.value, dut.in_word.value, dut.in_a.value = valid, word, a
        await FallingEdge(dut.clk)
        if valid:
            got = int(dut.quad.value)
            assert got == model.quad(word, a), f"cycle {cyc} a={a}: {got:#010x} != {model.quad(word, a):#010x}"
            seen[a] += 1
        model.clock(valid, word)
        await RisingEdge(dut.clk)
    assert all(n > 100 for n in seen), seen


@cocotb.test()
async def row_runs_with_prime_and_bubbles(dut):
    """The way ctrl/fetch use it (DESIGN 5, 7.1): run start A, a = A mod 4; for a != 0 one prime
    word loads prev, then quad j is run bytes 4j..4j+3 (words arrive with random bubbles)."""
    await start(dut)
    rng = random.Random(5)
    mem = bytes(rng.randrange(256) for _ in range(256))
    for trial in range(200):
        A = rng.randrange(8, 128)
        nq = rng.randrange(1, 12)
        a = A & 3
        run = mem[A:A + 4 * nq]
        base = A - a
        words = [int.from_bytes(mem[base + 4 * k:base + 4 * k + 4], "little") for k in range(nq + 1)]
        seq = words if a else words[:nq]
        got = b""
        for idx, w in enumerate(seq):
            for _ in range(rng.randrange(0, 3)):          # bubble: junk, not valid
                dut.in_valid.value, dut.in_word.value, dut.in_a.value = 0, rng.getrandbits(32), rng.randrange(4)
                await RisingEdge(dut.clk)
            dut.in_valid.value, dut.in_word.value, dut.in_a.value = 1, w, a
            await FallingEdge(dut.clk)
            if a == 0 or idx > 0:
                got += int(dut.quad.value).to_bytes(4, "little")
            await RisingEdge(dut.clk)
        assert got == run, f"trial {trial} A={A}"


def test_mcuaccel_align():
    run_cocotb("mcuaccel_align", rtl("accel/mcuaccel_align.sv"), "test_mcuaccel_align",
               Path(__file__).parent, "unit_mcuaccel_align")
