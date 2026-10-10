"""mcuaccel_ctrl (DESIGN 8.5, 6.3, 7.1): the sequencer, driven through its rd_*/st_*/resp_* ports by a
Python model of mcuaccel_fetch + the requant credit loop. The request stream (DESC/WBUF/PBUF reads,
X reads with their tags, tile pushes, STAMP writes) is compared with the golden stream derived from the
Python executor trace (backend/mcuaccel_backend/exec.py, via common.models.ctrl_golden).

Fetch model: one request outstanding; a request may be accepted in a cycle with no outstanding request
or in the cycle its predecessor's response is delivered; st (STAMP) has priority over rd. zero-wait:
accept at once, response one cycle later; stall: accept with p = 0.7, response latency 1..4. In the
cycle an err response is delivered, a request already presented by ctrl is accepted (it was raised
before ctrl could know about the error). fetch_idle is 0 while a request is outstanding (including
its response cycle) or presented.

Driving scheme: every input is driven at the FallingEdge, outputs are sampled 1 ns later (so that
combinational outputs settle), then accepts are decided and combinational outputs sampled again.
"""
import os
import random
import sys
from array import array
from pathlib import Path

import cocotb
import numpy as np
import pytest
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import models as M  # noqa: E402
from common.runner import ROOT, rtl, run_cocotb  # noqa: E402

sys.path.insert(0, str(ROOT / "backend/tests"))      # helpers.image (memory-image builder)

RTL = ROOT / "rtl/accel/mcuaccel_ctrl.sv"
pytestmark = pytest.mark.skipif(not RTL.exists(), reason="RTL not yet written (M2)")
FULL = os.environ.get("MCUACCEL_FULL") == "1"
PS = [8, 4, 1, 16] if FULL else [8, 4]

START_CYCLE = 3


# ------------------------------------------------------------------------------ layers / images
def make_layer(mode, H, W, C, O, KH, KW, SH, SW, PH, PW, seed, x_zp=-7, y_zp=-128, amin=-128, amax=127):
    from mcuaccel_backend.layers import Layer
    r = np.random.default_rng(seed)
    Cg = 1 if mode == "dw" else C
    w = array("b", r.integers(-128, 128, O * KH * KW * Cg).astype(np.int8).tobytes())
    L = Layer(mode, H, W, C, O, KH, KW, SH, SW, PH, PW, x_zp, y_zp, amin, amax, w,
              [int(b) for b in r.integers(-5000, 5000, O)], [int(m) for m in r.integers(1 << 30, 1 << 31, O)],
              [int(s) for s in r.integers(-10, 0, O)])
    x = r.integers(-128, 128, H * W * C).astype(np.int8).tobytes()
    return L, x


def build_cases(P):
    """-> [(name, mem, d_addr)]  (backend images: x, y, weights, params, descriptors in one memory)."""
    from helpers import image
    spec = [
        ("conv3x3_c3_o16_pad1", make_layer("conv", 4, 4, 3, 16, 3, 3, 1, 1, 1, 1, 1), 1, 2),
        ("dw3x3_c8_s2", make_layer("dw", 6, 6, 8, 8, 3, 3, 2, 2, 1, 1, 2, x_zp=5, amin=0), 0, 1),
        ("fc_k20_m10", make_layer("fc", 1, 2, 20, 10, 1, 1, 1, 1, 0, 0, 3, y_zp=3), 0, 0),
        ("fc_k20_m10_misaligned", make_layer("fc", 1, 3, 20, 10, 1, 1, 1, 1, 0, 0, 4), 3, 1),
        ("conv3x3_c5_o9_x_align3", make_layer("conv", 3, 3, 5, 9, 3, 3, 1, 1, 0, 0, 5), 3, 3),
        ("conv2x2_c7_s2_pad1_o4", make_layer("conv", 5, 4, 7, 4, 2, 2, 2, 1, 1, 0, 6, amax=60), 2, 0),
        ("split_1x1_c1_o513", make_layer("conv", 1, 1, 1, 513, 1, 1, 1, 1, 0, 0, 7), 1, 1),
    ]
    out = []
    for name, (L, x), xa, ya in spec:
        mem, y_addr, ny, d_addr, cmds = image(L, P, xa, ya, x, seed=11)
        out.append((name, mem, d_addr, len(cmds)))
    return out


# ------------------------------------------------------------------------------- environment
class Res:
    def __init__(self):
        self.reqs, self.req_cyc, self.pushes = [], [], []
        self.cmd_done, self.chain_done, self.err = [], [], []
        self.err_addr = None
        self.err_resp_cycle = None
        self.err_resp_addr = None
        self.stamps = []              # (first_raise_cycle, st_raise_cycle, st_data, st_addr)
        self.start_cycle = START_CYCLE
        self.cycles = 0


class Env:
    def __init__(self, dut, P, stall=False, seed=1):
        self.dut, self.P, self.stall = dut, P, stall
        self.rng = random.Random(seed)
        self.mem = bytearray()

    async def init(self):
        d = self.dut
        for s in ("start", "rd_accept", "st_accept", "resp_valid", "resp_we", "resp_err", "fetch_idle",
                  "mac_idle", "rq_idle", "wr_idle", "credit_ret"):
            getattr(d, s).value = 0
        d.desc_addr.value = d.resp_rdata.value = d.resp_addr.value = d.resp_tag.value = 0
        d.rst.value = 1
        cocotb.start_soon(Clock(d.clk, 10, unit="ns").start())
        for _ in range(3):
            await RisingEdge(d.clk)
        d.rst.value = 0
        await RisingEdge(d.clk)

    def word(self, addr):
        if 0 <= addr and addr + 4 <= len(self.mem):
            return int.from_bytes(self.mem[addr:addr + 4], "little")
        return None

    async def run_chain(self, d_addr, golden, err_at=None, extra_starts=(), max_cycles=60_000, tail=30,
                        stall=None):
        d, rng = self.dut, self.rng
        stall = self.stall if stall is None else stall
        res = Res()
        d.desc_addr.value = d_addr
        r = 0
        outst = None                       # dict(due, kind, addr, tag, data, be, err)
        retq = []                          # relative cycles at which credit_ret is driven
        pushed = ret = 0
        last_ret = last_lastx = -100
        n_acc = 0
        prev = {"rd": None, "st": None}    # payload presented last cycle and not accepted
        raise_c = {"rd": None, "st": None}
        cur_cmd = 0
        first_raise = {}
        busy_seen_high = False
        end_c = None
        err_c = None
        prev_pulse = {"cmd_done": 0, "chain_done": 0, "err": 0}
        while True:
            await FallingEdge(d.clk)
            # ---- 1. autonomous inputs
            resp_now = outst if (outst is not None and outst["due"] <= r) else None
            if resp_now is not None:
                assert resp_now["due"] == r
                d.resp_valid.value, d.resp_we.value = 1, int(resp_now["kind"] == "st")
                d.resp_err.value, d.resp_addr.value, d.resp_tag.value = int(resp_now["err"]), resp_now["addr"], resp_now["tag"]
                w = None if resp_now["kind"] == "st" or resp_now["err"] else self.word(resp_now["addr"])
                d.resp_rdata.value = rng.getrandbits(32) if w is None else w
            else:
                d.resp_valid.value = 0
                d.resp_we.value, d.resp_err.value = rng.getrandbits(1), rng.getrandbits(1)
                d.resp_rdata.value, d.resp_addr.value, d.resp_tag.value = (rng.getrandbits(32), rng.getrandbits(32),
                                                                           rng.getrandbits(20))
            cr = int(bool(retq) and retq[0] <= r)
            if cr:
                retq.pop(0)
                ret += 1
                last_ret = r
            d.credit_ret.value = cr
            rq_idle = int(pushed == ret and r > last_ret + 1)
            mac_idle = int(r > last_lastx + 3)
            wr_idle = int(rq_idle and r > last_ret + 5)
            d.rq_idle.value, d.mac_idle.value, d.wr_idle.value = rq_idle, mac_idle, wr_idle
            d.fetch_idle.value = int(outst is None)
            d.start.value = int(r == START_CYCLE or r in extra_starts)
            d.rd_accept.value = d.st_accept.value = 0
            await Timer(1, unit="ns")
            # ---- 2. sample ctrl request outputs
            rv, sv = int(d.rd_valid.value), int(d.st_valid.value)
            rd = (int(d.rd_addr.value), int(d.rd_tag.value))
            st = (int(d.st_addr.value), int(d.st_data.value))
            assert not (rv and sv), f"cycle {r}: rd_valid and st_valid together"
            if err_c is not None and r > err_c:
                assert not rv and not sv, f"cycle {r}: new request after an err response"
            for kind, v, pl in (("rd", rv, rd), ("st", sv, st)):
                if prev[kind] is not None:
                    assert v and pl == prev[kind], (
                        f"cycle {r}: {kind} request withdrawn or changed before accept "
                        f"({prev[kind]} -> {pl if v else None})")
                if v and raise_c[kind] is None:
                    raise_c[kind] = r
            if (rv or sv) and cur_cmd not in first_raise:
                first_raise[cur_cmd] = r
            if sv and prev["st"] is None:
                assert outst is None and rq_idle and mac_idle and wr_idle and pushed == ret, (
                    f"cycle {r}: STAMP raised before the pipeline is flushed "
                    f"(outstanding={outst is not None} rq_idle={rq_idle} mac_idle={mac_idle} wr_idle={wr_idle})")
            # ---- 3. fetch model: accept
            can = outst is None or resp_now is not None
            force = resp_now is not None and resp_now["err"]
            accept = None
            if can and (rv or sv) and (not stall or force or rng.random() < 0.7):
                accept = "st" if sv else "rd"
            resp_done = resp_now
            if resp_now is not None:
                outst = None
            if accept == "rd":
                d.rd_accept.value = 1
            elif accept == "st":
                d.st_accept.value = 1
            d.fetch_idle.value = int(outst is None and accept is None and not (rv or sv))
            await Timer(1, unit="ns")
            assert (int(d.rd_valid.value), int(d.st_valid.value)) == (rv, sv) and \
                (int(d.rd_addr.value), int(d.rd_tag.value)) == rd and \
                (int(d.st_addr.value), int(d.st_data.value)) == st, f"cycle {r}: request outputs changed with fetch_idle/accept"
            # ---- 4. record the accepted request, pulses
            if accept is not None:
                kind = accept
                addr, x = rd if kind == "rd" else st
                inj = err_at is not None and err_at == n_acc
                res.reqs.append((kind, addr, x if kind == "rd" else 0, x if kind == "st" else 0))
                res.req_cyc.append((raise_c[kind], r))
                if kind == "rd" and (x & 3) == M.DEST_X and (x >> 2 & 1):
                    # active X request: credit rule and descriptor-derived outputs
                    if x >> 10 & 1:
                        assert pushed - ret <= 1, f"cycle {r}: last request issued with no credit (in flight {pushed - ret})"
                        last_lastx = r
                    if cur_cmd < len(golden) and err_at is None:
                        dd = golden[cur_cmd]["dd"]
                        got = (int(d.dw.value), s8(int(d.x_zp.value)), s8(int(d.y_zp.value)),
                               s8(int(d.act_min.value)), s8(int(d.act_max.value)))
                        want = (dd["dw"], dd["x_zp"], dd["y_zp"], dd["act_min"], dd["act_max"])
                        assert got == want, f"cycle {r}: dw/x_zp/y_zp/act_min/act_max = {got}, expected {want}"
                if kind == "st":
                    first = first_raise.get(cur_cmd, r)
                    res.stamps.append((first, raise_c["st"], x, addr))
                    cur_cmd += 1
                    w = x & M.M32
                    if 0 <= addr and addr + 4 <= len(self.mem):
                        self.mem[addr:addr + 4] = w.to_bytes(4, "little")
                lat = rng.randint(1, 4) if stall else 1
                outst = dict(due=r + lat, kind=kind, addr=addr, tag=x if kind == "rd" else 0, err=inj)
                if inj:
                    res.err_resp_addr = addr
                n_acc += 1
                prev[kind] = None
                raise_c[kind] = None
            for kind, v, pl in (("rd", rv, rd), ("st", sv, st)):
                if accept != kind:
                    prev[kind] = pl if v else None
            if resp_done is not None and resp_done["err"]:
                err_c = r
                res.err_resp_cycle = r
                retq.clear()
                pushed = ret = 0
            if int(d.tile_push.value):
                res.pushes.append((r, int(d.tile_ch0.value), int(d.tile_nv.value), int(d.tile_yaddr.value)))
                pushed += 1
                retq.append(max((retq[-1] + 1) if retq else 0, r + rng.randrange(6, 15)))
            busy = int(d.busy.value)
            for name, lst in (("cmd_done", res.cmd_done), ("chain_done", res.chain_done), ("err", res.err)):
                v = int(getattr(d, name).value)
                if v:
                    assert not prev_pulse[name], f"cycle {r}: {name} is not a 1-cycle pulse"
                    lst.append(r)
                    if name == "err":
                        res.err_addr = int(d.err_addr.value)
                prev_pulse[name] = v
            if r >= START_CYCLE + 3 and end_c is None:
                assert busy or res.chain_done or res.err, f"cycle {r}: busy dropped before the chain ended"
            if busy:
                busy_seen_high = True
            if r == START_CYCLE + 2:
                assert busy, "busy not high 2 cycles after START"
            if end_c is None and (res.chain_done or res.err):
                end_c = r
            if end_c is not None and r > end_c + 3:
                assert not busy, f"cycle {r}: busy still high after chain end"
            if end_c is not None and r > end_c + 2:
                assert not rv and not sv and not int(d.tile_push.value), f"cycle {r}: activity after the chain ended"
            if end_c is not None:
                assert not (res.err and res.chain_done), "chain_done together with err (ERR leaves DONE = 0)"
            r += 1
            assert r < max_cycles, "timeout: chain never ended"
            if end_c is not None and r > end_c + tail and outst is None:
                break
            await RisingEdge(d.clk)
        res.cycles = r
        res.busy_seen = busy_seen_high
        await RisingEdge(d.clk)
        return res


def s8(v):
    v &= 0xFF
    return v - 256 if v & 0x80 else v


def verify_full(res, golden):
    n = M.check_requests(golden, res.reqs)
    assert n == len(res.reqs)
    assert len(res.err) == 0, "unexpected err pulse"
    assert len(res.cmd_done) == len(golden), f"cmd_done pulses {len(res.cmd_done)} != {len(golden)}"
    assert len(res.chain_done) == 1, f"chain_done pulses {len(res.chain_done)}"
    assert res.chain_done[0] >= res.cmd_done[-1]
    pos, k = 0, 0
    for ci, c in enumerate(golden):
        base = pos + 17 + len(c["wbuf"]) + len(c["pbuf"])
        want = c["pushes"]
        got = res.pushes[k:k + len(want)]
        assert len(got) == len(want), f"cmd {ci}: {len(got)} tile pushes, expected {len(want)}"
        for t, (g, w) in enumerate(zip(got, want)):
            assert g[1:] == w[:3], f"cmd {ci} push {t}: (ch0, nv, yaddr) = {g[1:]}, expected {w[:3]}"
            raised, acc = res.req_cyc[base + w[3]]
            assert raised <= g[0] <= acc + 1, (
                f"cmd {ci} push {t}: tile_push at {g[0]}, its last request was raised {raised}, accepted {acc}")
        k += len(want)
        pos = base + len(c["x"]) + 1
        first, st_raise, data, addr = res.stamps[ci]
        assert data == st_raise - first, f"cmd {ci}: STAMP {data}, cycles from first DESC raise to STAMP raise {st_raise - first}"
        assert res.cmd_done[ci] >= res.req_cyc[pos - 1][1], f"cmd {ci}: cmd_done before the STAMP write was accepted"
    assert k == len(res.pushes), "extra tile pushes"


def verify_abort(res, golden):
    M.check_requests(golden, res.reqs, partial=True)
    assert len(res.err) == 1, f"err pulses: {len(res.err)}"
    assert res.err_addr == res.err_resp_addr, f"err_addr {res.err_addr:#x} != {res.err_resp_addr:#x}"
    assert not res.chain_done
    assert all(c <= res.err[0] for c in res.cmd_done)
    stamps_ok = sum(1 for q in res.reqs if q[0] == "st") - (1 if res.err_resp_addr in {c["stamp_addr"] for c in golden} else 0)
    assert len(res.cmd_done) == stamps_ok, f"cmd_done pulses {len(res.cmd_done)} != commands whose STAMP completed ({stamps_ok})"
    # tile pushes so far are a prefix of the golden pushes
    allp = [p[:3] for c in golden for p in c["pushes"]]
    assert [p[1:] for p in res.pushes] == allp[:len(res.pushes)]


# ---------------------------------------------------------------------------------------- tests
async def prepare(dut, stall, seed):
    P = int(os.environ["MCUACCEL_P"])
    env = Env(dut, P, stall, seed)
    await env.init()
    return P, env


@cocotb.test()
async def chains_zero_wait(dut):
    P, env = await prepare(dut, False, 1)
    for name, mem, d_addr, ncmd in build_cases(P):
        golden = M.ctrl_golden(mem, d_addr, P)
        assert len(golden) == ncmd
        for rep in range(2 if name.startswith("conv3x3_c3") else 1):    # a second run: no state left over
            env.mem = bytearray(mem)
            res = await env.run_chain(d_addr, golden)
            verify_full(res, golden)
            dut._log.info(f"{name}: {len(res.reqs)} requests, {len(res.pushes)} tiles, {res.cycles} cycles")


@cocotb.test()
async def chains_stalling_fetch_and_start_while_busy(dut):
    P, env = await prepare(dut, True, 2)
    for k, (name, mem, d_addr, ncmd) in enumerate(build_cases(P)):
        if name.startswith("split"):
            continue
        golden = M.ctrl_golden(mem, d_addr, P)
        env.mem = bytearray(mem)
        # extra START pulses while BUSY must be ignored (no restart, no second chain_done)
        res = await env.run_chain(d_addr, golden, extra_starts=(START_CYCLE + 6, START_CYCLE + 40, START_CYCLE + 41))
        verify_full(res, golden)


@cocotb.test()
async def err_response_aborts_then_clean_chain(dut):
    P, env = await prepare(dut, False, 3)
    cases = {name: (mem, d_addr, ncmd) for name, mem, d_addr, ncmd in build_cases(P)}
    mem, d_addr, ncmd = cases["conv3x3_c3_o16_pad1"]
    golden = M.ctrl_golden(mem, d_addr, P)
    c = golden[0]
    nd, nw, npb, nx = 17, len(c["wbuf"]), len(c["pbuf"]), len(c["x"])
    # index of the nth accepted request: DESC, WBUF, PBUF, X..., STAMP
    points = [("desc_word_5", 5), ("desc_last", 16), ("wbuf_mid", nd + nw // 2), ("pbuf_first", nd + nw),
              ("x_first", nd + nw + npb), ("x_mid_tiles_in_flight", nd + nw + npb + nx // 2),
              ("x_near_end", nd + nw + npb + nx - 3), ("stamp_write", nd + nw + npb + nx)]
    for i, (label, n) in enumerate(points):
        for stall in ((False, True) if label == "x_mid_tiles_in_flight" else ((False,) if i % 2 == 0 else (True,))):
            env.mem = bytearray(mem)
            res = await env.run_chain(d_addr, golden, err_at=n, stall=stall)
            verify_abort(res, golden)
            # next chain must be bit exact (credits back to 2, FIFOs empty, counters reset)
            env.mem = bytearray(mem)
            res2 = await env.run_chain(d_addr, golden, stall=stall)
            verify_full(res2, golden)
    # err in the second command of a split chain, then a clean chain
    mem, d_addr, ncmd = cases["split_1x1_c1_o513"]
    golden = M.ctrl_golden(mem, d_addr, P)
    assert len(golden) == 2
    g0, g1 = golden
    n = 17 + len(g0["wbuf"]) + len(g0["pbuf"]) + len(g0["x"]) + 1 + 17 + len(g1["wbuf"]) + 1    # 2nd PBUF read of cmd 1
    env.mem = bytearray(mem)
    res = await env.run_chain(d_addr, golden, err_at=n)
    verify_abort(res, golden)
    assert len(res.cmd_done) == 1
    env.mem = bytearray(mem)
    res2 = await env.run_chain(d_addr, golden)
    verify_full(res2, golden)


@pytest.mark.parametrize("p", PS)
def test_mcuaccel_ctrl(p, monkeypatch):
    monkeypatch.setenv("MCUACCEL_P", str(p))
    run_cocotb("mcuaccel_ctrl", rtl("accel/mcuaccel_ctrl.sv"), "test_mcuaccel_ctrl",
               Path(__file__).parent, f"unit_mcuaccel_ctrl_p{p}", parameters={"P": p})
