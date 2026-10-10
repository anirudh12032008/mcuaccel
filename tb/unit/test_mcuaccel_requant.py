"""mcuaccel_requant (DESIGN 8.10, SPEC 4): bias + int8ops.requant + y_zp + signed clamp, driven
through the FIFO interface (tile_push at issue, acc_valid ~4+ cycles later, 2 credits), with
out_ready stalls and a synchronous-read pbuf model on the pb_* ports."""
import os
import random
import sys
from pathlib import Path

import cocotb
import pytest
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.models import INT32_MAX, INT32_MIN, nacc, requant_out, u32  # noqa: E402
from common.runner import ROOT, rtl, run_cocotb  # noqa: E402

RTL = ROOT / "rtl/accel/mcuaccel_requant.sv"
pytestmark = pytest.mark.skipif(not RTL.exists(), reason="RTL not yet written (M2)")
FULL = os.environ.get("MCUACCEL_FULL") == "1"
PS = [8, 1, 4, 16] if FULL else [8, 1]
N_RANDOM = 100_000 if FULL else 10_000

I32 = (INT32_MIN, -1, 0, 1, INT32_MAX)
MULTS = (INT32_MIN, -1, 0, 1 << 30, INT32_MAX)
SHIFTS = (-31, -1, 0, 1, 31)


class Bench:
    def __init__(self, dut, P, seed):
        self.dut, self.P, self.rng = dut, P, random.Random(seed)
        self.NA = nacc(P)
        self.cyc = 0
        self.table = [(0, 0, 0)] * 512            # (bias, mult, shift6) as signed, signed, 0..63
        self.credits = 2
        self.exp = []                             # expected output tuples (addr, byte, last, tile_id)
        self.tiles = []                           # per tile dict
        self.acc_q = []                           # (due, tile_id, vector)
        self.n_pushed = self.n_done = self.n_ret = 0
        self.ready_mode = ("always",)
        self.held = None
        self.prev_mid_tile = False
        self.out_cycles = 0
        self.idle_s = 1
        for s in ("acc_valid", "tile_push"):
            getattr(dut, s).value = 0
        for s in ("acc_in", "tile_ch0", "tile_nv", "tile_yaddr", "y_zp", "act_min", "act_max",
                  "pb_bias", "pb_mult", "pb_shift"):
            getattr(dut, s).value = 0
        dut.out_ready.value = 1
        dut.rst.value = 1

    async def start(self):
        d = self.dut
        cocotb.start_soon(Clock(d.clk, 10, unit="ns").start())
        cocotb.start_soon(self.pbuf_model())
        for _ in range(3):
            await RisingEdge(d.clk)
        d.rst.value = 0
        await RisingEdge(d.clk)

    async def pbuf_model(self):
        d = self.dut
        while True:
            await FallingEdge(d.clk)
            re, addr = int(d.pb_re.value), int(d.pb_raddr.value)
            await RisingEdge(d.clk)
            if re:
                bias, mult, sh = self.table[addr]
                d.pb_bias.value, d.pb_mult.value, d.pb_shift.value = u32(bias), u32(mult), sh

    def ready(self):
        m = self.ready_mode
        if m[0] == "always":
            return 1
        if m[0] == "p":
            return int(self.rng.random() < m[1])
        # bursty: long stalls
        if self.cyc % 40 < 25:
            return int(self.cyc % 3 == 0)
        return 1

    async def cycle(self, push=None):
        """One clock cycle. push = (ch0, nv, yaddr, accs) to tile_push this cycle (caller checked credits)."""
        d, r = self.dut, self.rng
        # ---- drive
        if push is not None:
            ch0, nv, yaddr, accs = push
            d.tile_push.value, d.tile_ch0.value, d.tile_nv.value, d.tile_yaddr.value = 1, ch0, nv, yaddr
            self.credits -= 1
            tid = len(self.tiles)
            exp = []
            for i in range(nv):
                bias, mult, sh = self.table[ch0 + i]
                exp.append((yaddr + i, requant_out(accs[i], bias, mult, sh, self.y_zp, self.amin, self.amax),
                            int(i == nv - 1), tid))
            self.exp += exp
            self.tiles.append(dict(push=self.cyc, acc=None, ret=None, last_out=None, nv=nv))
            self.n_pushed += 1
            prev_due = self.acc_q[-1][0] if self.acc_q else 0
            due = max(prev_due + 1, self.cyc + r.choice((4, 4, 5, 6, 9)))
            vec = list(accs) + [r.getrandbits(32) for _ in range(self.NA - nv)]   # junk beyond nv
            self.acc_q.append((due, tid, sum(u32(v) << (32 * i) for i, v in enumerate(vec))))
        else:
            d.tile_push.value = 0
            d.tile_ch0.value, d.tile_nv.value, d.tile_yaddr.value = r.getrandbits(9), r.randrange(1, 17), r.getrandbits(32)
        if self.acc_q and self.acc_q[0][0] <= self.cyc:
            _, tid, vec = self.acc_q.pop(0)
            d.acc_valid.value, d.acc_in.value = 1, vec
            self.tiles[tid]["acc"] = self.cyc
        else:
            d.acc_valid.value, d.acc_in.value = 0, r.getrandbits(32 * self.NA)
        rdy = self.ready()
        d.out_ready.value = rdy
        # ---- sample
        await FallingEdge(d.clk)
        ov, idle = int(d.out_valid.value), int(d.idle.value)
        self.idle_s = idle
        if int(d.credit_ret.value):
            assert self.n_ret < self.n_pushed, f"cycle {self.cyc}: credit_ret with no tile in flight"
            t = self.tiles[self.n_ret]
            assert t["acc"] is not None and t["acc"] < self.cyc, (
                f"cycle {self.cyc}: credit_ret for a tile whose acc vector has not been accepted")
            t["ret"] = self.cyc
            self.n_ret += 1
            self.credits += 1
            assert self.credits <= 2
        if ov:
            addr, byte, last = int(d.out_addr.value), int(d.out_byte.value), int(d.out_last.value)
            assert not idle, f"cycle {self.cyc}: idle while out_valid"
            if self.held is not None:
                assert (addr, byte, last) == self.held, f"cycle {self.cyc}: output changed while stalled"
            assert self.exp, f"cycle {self.cyc}: unexpected output {addr:#x}={byte:#x}"
            ea, eb, el, tid = self.exp[0]
            assert (addr, byte, last) == (ea, eb, el), (
                f"cycle {self.cyc}: got addr {addr:#x} byte {byte:#04x} last {last}; "
                f"expected {ea:#x} {eb:#04x} {el} (tile {tid})")
            assert self.tiles[tid]["acc"] is not None and self.tiles[tid]["acc"] < self.cyc
            if rdy:
                self.exp.pop(0)
                self.held = None
                self.out_cycles += 1
                if el:
                    t = self.tiles[tid]
                    assert t["ret"] is not None and t["ret"] < self.cyc, (
                        f"tile {tid}: credit_ret must precede its last output")
                    self.n_done += 1
            else:
                self.held = (addr, byte, last)
        else:
            assert self.held is None, f"cycle {self.cyc}: out_valid dropped while stalled"
            if self.ready_mode[0] == "always" and self.prev_mid_tile:
                raise AssertionError(f"cycle {self.cyc}: bubble inside a tile (1 value/cycle expected)")
        self.prev_mid_tile = bool(ov and rdy and not last and self.ready_mode[0] == "always")
        # a tile that was pushed (before this cycle) and is not fully written out keeps idle low
        if idle and (self.n_pushed - self.n_done) > 0 and not (push is not None and self.n_pushed - self.n_done == 1):
            raise AssertionError(f"cycle {self.cyc}: idle high with {self.n_pushed - self.n_done} tiles in flight")
        self.cyc += 1
        await RisingEdge(d.clk)

    async def settle(self):
        """No new tiles: run until everything was written out and idle is 1."""
        t0 = self.cyc
        while self.exp or self.acc_q or self.n_ret < self.n_pushed:
            await self.cycle()
            assert self.cyc - t0 < 20_000, "timeout draining (credit_ret / outputs missing?)"
        for _ in range(4):
            await self.cycle()
        assert self.idle_s == 1, "idle did not return 4 cycles after the last output"

    async def run(self, jobs, gap_p=0.0, y=(0, -128, 127), mode=("always",)):
        """jobs: [(ch0, nv, yaddr, accs)] pushed as credits allow."""
        d = self.dut
        await self.settle()
        self.y_zp, self.amin, self.amax = y
        d.y_zp.value, d.act_min.value, d.act_max.value = y[0] & 255, y[1] & 255, y[2] & 255
        self.ready_mode = mode
        await self.cycle()
        t0, i = self.cyc, 0
        while i < len(jobs):
            if self.credits > 0 and self.rng.random() >= gap_p:
                await self.cycle(jobs[i])
                i += 1
            else:
                await self.cycle()
            assert self.cyc - t0 < 200_000, "timeout: credits never returned?"
        await self.settle()


def split_tiles(outs, P, rng, base=0, yaddr=0x8000_0000):
    """outs: [(acc, bias, mult, shift6)] -> (table fill, jobs). nv in 1..P (or 4 for dw-like)."""
    jobs, i, pos = [], 0, base
    while i < len(outs):
        nv = min(len(outs) - i, rng.choice([n for n in range(1, P + 1)] + [4]))
        if pos + nv > 512:
            pos = 0
        jobs.append((pos, nv, yaddr, [o[0] for o in outs[i:i + nv]], [o[1:] for o in outs[i:i + nv]]))
        pos += nv
        yaddr += rng.choice((nv, 64))
        i += nv
    return jobs


async def run_outs(b, outs, y, gap_p=0.0, mode=("always",)):
    """Place per-output (bias, mult, shift6) in the pbuf table and run all outputs."""
    await b.settle()
    assert len(outs) <= 512
    jobs = split_tiles(outs, b.P, b.rng, base=b.rng.randrange(0, 512 - len(outs) + 1))
    for ch0, nv, ya, accs, params in jobs:
        for i, prm in enumerate(params):
            b.table[ch0 + i] = prm
    await b.run([(ch0, nv, ya, accs) for ch0, nv, ya, accs, _ in jobs], gap_p, y, mode)


@cocotb.test()
async def directed_edge_cases(dut):
    P = int(os.environ["MCUACCEL_P"])
    b = Bench(dut, P, 3)
    await b.start()
    grid = []
    for acc in I32:
        for mult in MULTS:
            for sh in SHIFTS:
                for bias in (0, 1, INT32_MIN, INT32_MAX):
                    grid.append((acc, bias, mult, sh & 63))
    # MIN * MIN reaching the multiplier (av == INT32_MIN) in every way
    special = [(INT32_MIN, 0, INT32_MIN, s & 63) for s in (0, -1, -5, -31)]
    special += [(-(1 << 30), 0, INT32_MIN, 1), (-(1 << 29), 0, INT32_MIN, 2), (-1, 0, INT32_MIN, 31),
                (INT32_MAX, 1, INT32_MIN, 0), (INT32_MIN, -1, INT32_MAX, 0), (INT32_MIN, 0, INT32_MIN, 0)]
    outs = grid + special
    settings = [(0, -128, 127),            # no clamp
                (127, -128, 127),           # y_zp max: r = INT32_MAX + 127 must clamp, not wrap
                (-128, -128, 127),          # y_zp min
                (5, 5, 5), (-128, -128, -128), (127, 127, 127),      # clamp bounds equal
                (127, -128, 100), (-128, -100, 127), (0, 0, 127)]    # relu-like / one-sided
    for k, y in enumerate(settings):
        mode = [("always",), ("p", 0.5), ("always",)][k % 3]
        await run_outs(b, outs, y, gap_p=0.0 if k % 2 == 0 else 0.4, mode=mode)
    await b.settle()
    assert b.n_done == len(b.tiles) and b.n_ret == b.n_pushed


@cocotb.test()
async def random_batch(dut):
    P = int(os.environ["MCUACCEL_P"])
    b = Bench(dut, P, 100 + P)
    await b.start()
    r = b.rng
    total = 0
    while total < N_RANDOM:
        n = r.randrange(80, 300)
        outs = []
        for _ in range(n):
            u = r.random()
            acc = r.choice(I32) if u < .1 else r.randint(-(1 << 20), 1 << 20) if u < .55 else r.randint(INT32_MIN, INT32_MAX)
            u = r.random()
            bias = 0 if u < .15 else r.randint(-(1 << 20), 1 << 20) if u < .75 else r.randint(INT32_MIN, INT32_MAX)
            u = r.random()
            mult = r.randint(1 << 30, INT32_MAX) if u < .7 else r.choice(MULTS) if u < .8 else r.randint(INT32_MIN, INT32_MAX)
            sh = r.randint(-31, 31) if r.random() < .3 else r.randint(-12, 2)
            outs.append((acc, bias, mult, sh & 63))
        lo, hi = sorted(r.randint(-128, 127) for _ in range(2))
        if r.random() < .4:
            lo, hi = -128, 127
        y = (r.randint(-128, 127), lo, hi)
        mode = r.choice([("always",), ("p", 0.5), ("p", 0.9), ("burst",)])
        await run_outs(b, outs, y, gap_p=r.choice((0.0, 0.0, 0.3, 0.8)), mode=mode)
        total += n
    await b.settle()
    assert b.n_done == len(b.tiles) and not b.exp


@cocotb.test()
async def throughput_one_value_per_cycle(dut):
    """Single full tile, out_ready = 1: the nv outputs leave on consecutive cycles (checked in
    Bench.cycle) and idle returns afterwards."""
    P = int(os.environ["MCUACCEL_P"])
    b = Bench(dut, P, 9)
    await b.start()
    r = b.rng
    for _ in range(20):
        nv = r.choice([P, 4]) if P >= 4 else 4
        outs = [(r.randint(-(1 << 20), 1 << 20), r.randint(-1000, 1000), r.randint(1 << 30, INT32_MAX),
                 r.randint(-10, 0) & 63) for _ in range(nv)]
        for i, o in enumerate(outs):
            b.table[i] = o[1:]
        await b.settle()
        await b.run([(0, nv, 0x1000, [o[0] for o in outs])], 0.0, (3, -128, 127), ("always",))
    assert b.out_cycles >= 20 * 4


@pytest.mark.parametrize("p", PS)
def test_mcuaccel_requant(p, monkeypatch):
    monkeypatch.setenv("MCUACCEL_P", str(p))
    run_cocotb("mcuaccel_requant", rtl("accel/mcuaccel_requant.sv"), "test_mcuaccel_requant",
               Path(__file__).parent, f"unit_mcuaccel_requant_p{p}", parameters={"P": p})
