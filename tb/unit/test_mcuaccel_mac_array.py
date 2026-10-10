"""mcuaccel_mac_array (DESIGN 8.9, 7.3): S1/S2 arithmetic vs the Python model in conv and dw mode,
masks, first/last, back-to-back tiles, 2-cycle in->out_valid latency, wrap at 2^32, idle."""
import os
import random
import sys
from pathlib import Path

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.models import MacModel, nacc, s8, u32  # noqa: E402
from common.runner import ROOT, rtl, run_cocotb  # noqa: E402

RTL = ROOT / "rtl/accel/mcuaccel_mac_array.sv"
pytestmark = pytest.mark.skipif(not RTL.exists(), reason="RTL not yet written (M2)")
FULL = os.environ.get("MCUACCEL_FULL") == "1"
PS = [8, 1, 4, 16]


def pack_w(ws):
    return sum(u32(w) << (32 * p) for p, w in enumerate(ws))


class Bench:
    def __init__(self, dut, P):
        self.dut, self.P = dut, P
        self.cyc = 0
        self.pending = []            # (due_cycle, expected acc list)
        self.checked = 0
        self.rng = random.Random(1234 + P)
        for s in ("in_valid", "in_first", "in_last", "dw"):
            getattr(dut, s).value = 0
        dut.in_quad.value = dut.in_mask.value = dut.x_zp.value = dut.in_w.value = 0
        dut.rst.value = 1

    async def start(self):
        d = self.dut
        cocotb.start_soon(Clock(d.clk, 10, unit="ns").start())
        for _ in range(3):
            await RisingEdge(d.clk)
        d.rst.value = 0
        await RisingEdge(d.clk)

    async def tick(self, valid=0, first=0, last=0, quad=0, mask=0, w=None):
        """One cycle: drive (right after a RisingEdge), sample at the FallingEdge, check outputs.
        Invalid cycles carry junk on every data input and on first/last."""
        d, P, r = self.dut, self.P, self.rng
        d.in_valid.value = valid
        d.in_first.value, d.in_last.value = (first, last) if valid else (r.getrandbits(1), r.getrandbits(1))
        d.in_quad.value = quad if valid else r.getrandbits(32)
        d.in_mask.value = mask if valid else r.getrandbits(4)
        d.in_w.value = pack_w(w) if valid else r.getrandbits(32 * P)
        await FallingEdge(d.clk)
        ov, idle = int(d.out_valid.value), int(d.idle.value)
        self.idle_s = idle
        if ov:
            assert self.pending and self.pending[0][0] == self.cyc, (
                f"cycle {self.cyc}: out_valid without a last beat exactly 2 cycles earlier")
            _, exp = self.pending.pop(0)
            acc = int(d.out_acc.value)
            for i, e in enumerate(exp):
                got = (acc >> (32 * i)) & 0xFFFF_FFFF
                assert got == u32(e), f"cycle {self.cyc}: acc[{i}] = {got:#010x}, expected {u32(e):#010x}"
            self.checked += 1
            assert not idle, "idle high while out_valid"
        elif self.pending and self.pending[0][0] <= self.cyc:
            raise AssertionError(f"cycle {self.cyc}: expected out_valid")
        if self.beat_prev and idle:
            raise AssertionError(f"cycle {self.cyc}: idle high one cycle after a valid beat")
        self.beat_prev = valid
        self.cyc += 1
        await RisingEdge(d.clk)

    beat_prev = 0
    idle_s = 1

    async def drain(self):
        """Idle cycles until idle=1 (as sampled) and every expected out_valid was seen."""
        for _ in range(12):
            await self.tick()
            if self.idle_s and not self.pending:
                return
        raise AssertionError("idle never returned (or out_valid missing)")

    async def run_tile(self, model, beats, dw, x_zp, gap_p):
        """beats: list of (quad, mask, ws). first on beat 0, last on the final beat."""
        for i, (quad, mask, ws) in enumerate(beats):
            while self.rng.random() < gap_p:
                await self.tick()
            first, last = int(i == 0), int(i == len(beats) - 1)
            exp = model.beat(quad, mask, x_zp, ws, dw, first, last)
            if last:
                self.pending.append((self.cyc + 2, exp))
            await self.tick(1, first, last, quad, mask, ws)


def rand_data(rng, P, kind):
    if kind == "extreme":
        pick = lambda: rng.choice((0x80, 0x7F, 0x00, 0xFF, 0x01))  # noqa: E731
        q = sum(pick() << (8 * l) for l in range(4))
        ws = [sum(pick() << (8 * l) for l in range(4)) for _ in range(P)]
    else:
        q = rng.getrandbits(32)
        ws = [rng.getrandbits(32) for _ in range(P)]
    mask = 0xF if rng.random() < .5 else rng.getrandbits(4)
    return q, mask, ws


@cocotb.test()
async def random_tiles_both_modes(dut):
    P = int(os.environ["MCUACCEL_P"])
    b = Bench(dut, P)
    await b.start()
    rng = random.Random(77 + P)
    model = MacModel(P)
    segs = 24 if not FULL else 120
    for seg in range(segs):
        dw = int(seg % 2 == 1)
        x_zp = rng.choice((0x80, 0x7F, 0x00, 0xFF)) if rng.random() < .3 else rng.getrandbits(8)
        dut.dw.value, dut.x_zp.value = dw, x_zp          # constant while the pipeline is busy
        gap_p = rng.choice((0.0, 0.0, 0.3))
        for _ in range(rng.randrange(6, 16)):
            n = rng.choice((1, 1, 2, 3, 5, rng.randrange(1, 40)))
            kind = "extreme" if rng.random() < .3 else "rand"
            beats = [rand_data(rng, P, kind) for _ in range(n)]
            await b.run_tile(model, beats, dw, x_zp, gap_p)
        await b.drain()
        for _ in range(rng.randrange(0, 3)):
            await b.tick()
    assert b.checked >= segs * 6 and not b.pending


@cocotb.test()
async def wrap_at_2_32(dut):
    """Accumulators wrap mod 2^32 (SPEC 4 rule 3): one long tile of maximal products."""
    P = int(os.environ["MCUACCEL_P"])
    b = Bench(dut, P)
    await b.start()
    model = MacModel(P)
    n = 17_000                                           # 4 * 255 * 128 * n > 2^31
    for sign in (1, -1):
        x, xzp = (0x80, 0x7F) if sign > 0 else (0x7F, 0x80)    # xs = -255 / +255
        w = 0x80808080                                          # -128
        beats = [(x * 0x01010101, 0xF, [w] * P)] * n
        dut.dw.value, dut.x_zp.value = 0, xzp
        await b.run_tile(model, beats, 0, xzp, 0.0)
        await b.drain()
        exact = n * 4 * 255 * 128 * sign
        assert abs(exact) > (1 << 31)
        assert b.checked == (1 if sign > 0 else 2)
    if P == 8:                                           # dw: one product per beat, 66k beats
        n = 67_000
        for sign in (1, -1):
            x, xzp = (0x80, 0x7F) if sign > 0 else (0x7F, 0x80)
            beats = [(x * 0x01010101, 0xF, [0x80808080] + [0] * (P - 1))] * n
            dut.dw.value, dut.x_zp.value = 1, xzp
            await b.run_tile(model, beats, 1, xzp, 0.0)
            await b.drain()
        assert b.checked == 4


@cocotb.test()
async def back_to_back_single_beat_tiles_and_idle(dut):
    """first and last on the same beat, every cycle: out_valid every cycle, 2 cycles later."""
    P = int(os.environ["MCUACCEL_P"])
    b = Bench(dut, P)
    await b.start()
    rng = random.Random(5)
    model = MacModel(P)
    await b.drain()
    assert b.idle_s == 1
    dut.dw.value, dut.x_zp.value = 0, 0x11
    beats = [rand_data(rng, P, "rand") for _ in range(200)]
    for q, m, ws in beats:
        await b.run_tile(model, [(q, m, ws)], 0, 0x11, 0.0)
    await b.drain()
    assert b.checked == 200


@pytest.mark.parametrize("p", PS)
def test_mcuaccel_mac_array_p(p, monkeypatch):
    monkeypatch.setenv("MCUACCEL_P", str(p))
    run_cocotb("mcuaccel_mac_array", rtl("accel/mcuaccel_mac_array.sv"), "test_mcuaccel_mac_array",
               Path(__file__).parent, f"unit_mcuaccel_mac_array_p{p}", parameters={"P": p})
