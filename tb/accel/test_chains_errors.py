"""Chains, errors, abort recovery, START while BUSY, CLEAR (DESIGN 6.1, 6.3, 10.2).

- chains: 2-4 NEXT-linked commands per START; independent random layers or dependent ones (the
  y of command j is the x of command j+1, which exercises write visibility across STAMP/NEXT);
  all outputs bit-exact, NCMD = number of descriptors, STAMP written for each.
- errors: a fault-free dry run logs the bus accesses; then, for chosen access indices, the RAM
  slave answers that word address with err (no effect). Expected: STATUS.ERR=1, DONE=0, BUSY=0,
  ERR_ADDR == address, no access at all after the faulting one (so no write), no byte changed
  outside y/STAMP, no hang; CLEAR clears ERR; then, WITHOUT a DUT reset, a fault-free chain is
  bit-exact (abort recovery, DESIGN 6.3).
- START while BUSY is ignored (same number of bus accesses as an undisturbed run, same result).
"""
import os
import sys
from pathlib import Path

import cocotb
import numpy as np
import pytest
from cocotb.triggers import ClockCycles

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from accel import harness as H  # noqa: E402
from accel.gen_layers import Item, build_image, gen_chain, make_layer  # noqa: E402

pytestmark = H.PYTESTMARK
NCHAIN = H.env_int("MCUACCEL_CHAINS", 12)
NERR = H.env_int("MCUACCEL_ERR_CHAINS", 4)
SLOW = H.env_int("MCUACCEL_SLOW", 0)


def stalls_for(i):
    return bool(np.random.default_rng(i + 4242).random() < .3)


@cocotb.test()
async def chains(dut):
    b = await H.Bench.create(dut)
    for i in range(NCHAIN):
        dep = i % 2 == 1                      # alternate independent / dependent chains
        items = gen_chain(i, n=2 + i % 3, dependent=dep)
        img = build_image(items, b.P, i)
        res = await b.run_image(img, stalls=stalls_for(i), seed=i)
        dut._log.info("chain %d dep=%d n=%d: ncmd=%d cycles=%d stamps=%s mismatches=%d",
                      i, dep, len(items), res.ncmd, res.cycles, res.stamps, res.mismatches)
        b.check_normal(res, img, f"chain {i} (dependent={dep}, {len(items)} layers)")
    b.finish("chains")


def fault_indices(n):
    idx = {0, 1, n // 4, n // 2, (3 * n) // 4, n - 2, n - 1}
    return sorted(i for i in idx if 0 <= i < n)


@cocotb.test()
async def errors(dut):
    b = await H.Bench.create(dut)
    c = b.check
    for i in range(NERR):
        items = gen_chain(1000 + i, n=1 + i % 3, dependent=(i % 2 == 1))
        img = build_image(items, b.P, 1000 + i)
        dry = await b.run_image(img, stalls=False, seed=i)
        b.check_normal(dry, img, f"errors chain {i} dry run")
        log = list(b.ram.log)
        if not log:
            c(False, f"errors chain {i}: dry run made no bus access")
            continue
        for k_idx in fault_indices(len(log)):
            fault = log[k_idx][0]
            k = next(j for j, (a, _) in enumerate(log) if a == fault)   # first access to the word
            for stalls in (False, True):
                lab = f"chain {i} fault@{k_idx} (word {fault:#x}, first access #{k}/{len(log)}, stall={int(stalls)})"
                res = await b.run_image(img, stalls=stalls, seed=k_idx, fault=fault)
                dut._log.info("%s: status=%#x err_addr=%#x ncmd=%d accesses=%d after_fault=%d",
                              lab, res.status, res.err_addr, res.ncmd, res.accesses, b.ram.after_fault)
                c(not res.timed_out, f"{lab}: hang")
                c(b.ram.hit, f"{lab}: fault address never accessed")
                c(res.status == H.ERR, f"{lab}: STATUS {res.status:#x}, expected ERR only (BUSY=0, DONE=0)")
                c(res.err_addr == fault, f"{lab}: ERR_ADDR {res.err_addr:#x} != {fault:#x}")
                c(b.ram.after_fault == 0, f"{lab}: {b.ram.after_fault} bus accesses after the err response")
                if not stalls:
                    c(res.accesses == k + 1, f"{lab}: {res.accesses} accesses, expected {k + 1}")
                c(res.ncmd < img.ncmds, f"{lab}: NCMD {res.ncmd} claims the whole chain completed")
                c(res.outside == 0, f"{lab}: {res.outside} bytes outside y/STAMP modified")
                c(int(dut.busy.value) == 0, f"{lab}: busy output still high")
                # CLEAR clears ERR (and DONE)
                await b.m.write(H.CTRL, H.CLEAR)
                st = (await b.m.read(H.STATUS))[0]
                c(st == 0, f"{lab}: STATUS {st:#x} after CLEAR")
                # abort recovery: next correct chain, no DUT reset
                rec = await b.run_image(img, stalls=stalls, seed=k_idx + 1, reset=False)
                b.check_normal(rec, img, f"{lab}: recovery run")
                b.chk_s.check_drained()
                b.chk_m.check_drained()
    b.finish("errors")


@cocotb.test()
async def error_sweep(dut):
    """Error at EVERY bus access index of a small layer, then a recovery chain without reset:
    ERR set, BUSY dropped, ctrl credit back at 2, recovery bit-exact (credit-return race at abort)."""
    import types
    b = await H.Bench.create(dut)
    c = b.check
    ram = b.ram
    orig = type(ram)._access
    st = {"n": None, "i": 0}

    def acc(self, addr, we, be, wdata):
        st["i"] += 1
        if st["n"] is not None and st["i"] == st["n"] and not self.hit:
            self.hit = True
            self.log.append((addr, we))
            return 0, 1
        return orig(self, addr, we, be, wdata)
    ram._access = types.MethodType(acc, ram)
    r = np.random.default_rng(7)
    L = make_layer(r, "conv", 3, 3, 4, 16, 1, 1, 1, 1, 0, 0)
    x = r.integers(-128, 128, 3 * 3 * 4).astype(np.int8).tobytes()
    img = build_image([Item(L, x, 0, 0)], b.P, 1)
    dry = await b.run_image(img, seed=1)
    b.check_normal(dry, img, "sweep dry run")
    ntot = st["i"]
    step = 1 if SLOW else 1 + ntot // 40          # fast subset unless MCUACCEL_SLOW
    for n in range(1, ntot + 1, step):
        st["n"], st["i"] = n, 0
        res = await b.run_image(img, seed=2, max_cycles=20000)
        cr = int(dut.u_ctrl.credit.value)
        st["n"] = None
        rec = await b.run_image(img, seed=3, reset=False, max_cycles=20000)
        lab = f"fault@access {n}/{ntot}"
        c(not res.timed_out and res.status == H.ERR, f"{lab}: status {res.status:#x} timeout={res.timed_out}")
        c(cr == 2, f"{lab}: credit {cr} after abort")
        c(not rec.timed_out, f"{lab}: recovery hung (BUSY stuck)")
        b.check_normal(rec, img, f"{lab}: recovery")
    b.finish("error_sweep")


@cocotb.test()
async def start_while_busy(dut):
    b = await H.Bench.create(dut)
    c = b.check
    r = np.random.default_rng(7)
    L = make_layer(r, "conv", 8, 8, 8, 16, 3, 3, 1, 1, 1, 1)
    x = r.integers(-128, 128, 8 * 8 * 8).astype(np.int8).tobytes()
    img = build_image([Item(L, x, 1, 2)], b.P, 7)
    base = await b.run_image(img, seed=1)
    b.check_normal(base, img, "undisturbed run")
    n0 = base.accesses
    res = await b.run_image(img, seed=1, extra_starts=3)
    b.check_normal(res, img, "run with START while BUSY")
    c(res.accesses == n0, f"START while BUSY changed behaviour: {res.accesses} vs {n0} bus accesses")
    await ClockCycles(dut.clk, 300)           # an ignored START must not restart the chain later
    st = (await b.m.read(H.STATUS))[0]
    n1 = (await b.m.read(H.NCMD))[0]
    c(st == H.DONE, f"STATUS {st:#x} 300 cycles after completion")
    c(n1 == img.ncmds, f"NCMD {n1} after completion")
    c(len(b.ram.log) == n0, "chain restarted after completion (extra bus accesses)")
    # CLEAR with nothing pending clears DONE, leaves registers readable
    await b.m.write(H.CTRL, H.CLEAR)
    c((await b.m.read(H.STATUS))[0] == 0, "CLEAR did not clear DONE")
    b.finish("start_while_busy")


def test_chains_errors():
    import json
    for P in H.ps_list():
        H.run_sim("test_chains_errors", P=P)
        for name in ("chains", "errors", "error_sweep", "start_while_busy"):
            s = H.read_summary(name)
            print(f"{name.upper()} P={P}: runs={s['runs']} problems={len(s['problems'])}")
            if not s["stub"]:
                assert not s["problems"]
