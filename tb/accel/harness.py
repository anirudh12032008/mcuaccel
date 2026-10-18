"""cocotb harness for mcuaccel_top alone (no CPU). DESIGN 8.1, 10.2.

Python owns the RAM image (FaultRam, 1 MiB at 0x8000_0000) and serves it to the DUT's master port
m_*; the slave port s_* is driven by BusMaster (CPU stand-in). BusChecker watches both ports.
Per seed: reset, load image, write DESC_ADDR, CTRL.START, poll STATUS until !BUSY, read CYCLES /
NCMD / ERR_ADDR and the STAMP words, compare the whole RAM with the expected image.

Environment:
  MCUACCEL_SEEDS (40)  seeds for test_random_layers; 1000 + MCUACCEL_SHAPES=full = campaign C2a
  MCUACCEL_PS ("8")    comma list of P values (one Verilator build per P)
  MCUACCEL_ALLOW_STUB=1 run against the stub top (no assertions on results, plumbing only)
  MCUACCEL_FULL=1      whole-network test over 10 images instead of 2
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.bus_checker import BusChecker, port  # noqa: E402
from common.bus_master import BusMaster  # noqa: E402
from common.ram_slave import RamSlave  # noqa: E402

from accel.gen_layers import RAM_BASE, RAM_SIZE, ROOT  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("MCUACCEL_OUT", ROOT / "build" / "accel"))
ALLOW_STUB = os.environ.get("MCUACCEL_ALLOW_STUB") == "1"
REAL_RTL = (ROOT / "rtl/accel/mcuaccel_ctrl.sv").exists()
STUB_MODE = ALLOW_STUB and not REAL_RTL
PYTESTMARK = pytest.mark.skipif(not (REAL_RTL or ALLOW_STUB),
                                reason="accelerator RTL not yet written (M2)")

A = 0x3000_0000
ID, CONFIG, CTRL, STATUS, DESC, CYCLES, NCMD, ERR_ADDR = (A + 4 * i for i in range(8))
BUSY, DONE, ERR = 1, 2, 4
START, CLEAR = 1, 2


def env_int(name, default):
    return int(os.environ.get(name, default))


def ps_list():
    return [int(v) for v in os.environ.get("MCUACCEL_PS", "8").split(",")]


# ---------------------------------------------------------------- pytest side
def run_sim(test_module, P=8, env=None, testcase=None):
    """Build mcuaccel_top (Verilator, parameter P; build shared by all accel test modules) and run
    the cocotb tests of `test_module` (file in tb/accel). Returns nothing; asserts no cocotb failure."""
    from cocotb_tools.check_results import get_results
    from cocotb_tools.runner import get_runner
    tag = f"accel_{'stub_' if STUB_MODE else ''}P{P}"
    build = ROOT / "build" / tag
    srcs = sorted((ROOT / "rtl/accel").glob("*.sv"))
    runner = get_runner("verilator")
    runner.build(sources=[str(s) for s in srcs], hdl_toplevel="mcuaccel_top",
                 build_dir=build / "sim", parameters={"P": P}, always=False,
                 build_args=["-Wno-fatal", "--timing", str(ROOT / "syn/lint.vlt")])
    e = {"MCUACCEL_OUT": str(OUT)}
    e.update(env or {})
    xml = runner.test(hdl_toplevel="mcuaccel_top", test_module=test_module, test_dir=str(HERE),
                      build_dir=build / "sim", results_xml=str(build / f"{test_module}.xml"),
                      extra_env=e, testcase=testcase)
    total, failed = get_results(Path(xml))
    assert total >= 1 and failed == 0, f"cocotb {test_module}: {failed}/{total} failed"


def read_summary(name):
    return json.loads((OUT / f"{name}.json").read_text())


# ---------------------------------------------------------------- results CSV
CSV_COLS = ["seed", "mode", "H", "W", "C", "O", "KH", "KW", "SH", "SW", "PH", "PW", "x_align",
            "y_align", "stalls", "cycles", "stamps", "mismatches", "commit", "date", "P"]


def csv_path():
    p = os.environ.get("MCUACCEL_CSV")
    if p:
        return Path(p)
    if STUB_MODE:
        return OUT / "m3_bitexact_stub.csv"
    if env_int("MCUACCEL_SEEDS", 40) >= 1000 and not os.environ.get("MCUACCEL_SMOKE"):
        return ROOT / "results" / "m3_bitexact.csv"          # the C2a campaign
    return OUT / "m3_bitexact.csv"


def _commit():
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=10).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


class ResultsCsv:
    """Appends rows; writes the header when the file is missing/empty. Truncation is done once by
    the pytest wrapper (reset_csv), because one campaign spans several simulations (one per P)."""

    def __init__(self, path=None):
        self.path = Path(path or csv_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.commit = _commit()
        if not self.path.exists() or self.path.stat().st_size == 0:
            self.path.write_text(",".join(CSV_COLS) + "\n")

    def add(self, seed, L, xa, ya, stalls, res, P):
        row = [seed, L.mode, L.H, L.W, L.C, L.O, L.KH, L.KW, L.SH, L.SW, L.PH, L.PW, xa, ya,
               int(stalls), res.cycles, ";".join(str(s) for s in res.stamps), res.mismatches,
               self.commit, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), P]
        with open(self.path, "a") as f:
            f.write(",".join(str(v) for v in row) + "\n")


def reset_csv():
    p = csv_path()
    if p.exists():
        p.unlink()


# ---------------------------------------------------------------- cocotb side
class FaultRam(RamSlave):
    """RamSlave that logs accesses and answers one chosen word address with err (no effect)."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.arm(None)

    def arm(self, fault):
        self.fault, self.hit, self.log, self.after_fault = fault, False, [], 0

    def _access(self, addr, we, be, wdata):
        if self.hit:
            self.after_fault += 1
        self.log.append((addr, we))
        if self.fault is not None and not self.hit and addr == self.fault:
            self.hit = True
            self.errors += 1
            return 0, 1
        return super()._access(addr, we, be, wdata)


@dataclass
class Result:
    status: int = 0
    cycles: int = 0
    ncmd: int = 0
    err_addr: int = 0
    stamps: list = field(default_factory=list)
    mismatches: int = 0
    y_mismatch: int = 0
    outside: int = 0
    timed_out: bool = False
    accesses: int = 0
    got: bytes = b""

    @property
    def done(self):
        return bool(self.status & DONE)


class Bench:
    def __init__(self, dut):
        self.dut = dut
        self.problems: list[str] = []
        self.runs = 0
        self.P = None

    @classmethod
    async def create(cls, dut):
        from common.bus_master import clock_reset
        self = cls(dut)
        await clock_reset(dut)
        self.ram = FaultRam(dut.clk, port(dut, "m_"), dut.rst, base=RAM_BASE, size=RAM_SIZE)
        self.ram.start()
        self.chk_s = BusChecker(dut.clk, port(dut, "s_"), dut.rst, name="slave port")
        self.chk_m = BusChecker(dut.clk, port(dut, "m_"), dut.rst, name="master port")
        self.chk_s.start()
        self.chk_m.start()
        self.m = BusMaster(dut.clk, port(dut, "s_"))
        cfg, _ = await self.m.read(CONFIG)
        self.P = cfg & 0xFF
        return self

    # --- bookkeeping
    def check(self, cond, msg):
        if not cond:
            self.problems.append(msg)
            self.dut._log.error("CHECK FAILED: %s", msg)
        return bool(cond)

    def finish(self, name):
        """Write the summary json; assert (real RTL) or just report (stub)."""
        self.chk_s.check_drained()
        self.chk_m.check_drained()
        OUT.mkdir(parents=True, exist_ok=True)
        summary = {"runs": self.runs, "problems": self.problems, "P": self.P,
                   "stub": STUB_MODE}
        (OUT / f"{name}.json").write_text(json.dumps(summary, indent=1))
        self.dut._log.info("%s: %d runs, %d problems (stub=%s)", name, self.runs,
                           len(self.problems), STUB_MODE)
        if not STUB_MODE:
            assert not self.problems, f"{len(self.problems)} problems, first: {self.problems[:5]}"

    # --- DUT control
    async def reset(self):
        from cocotb.triggers import ClockCycles, RisingEdge
        self.dut.rst.value = 1
        await ClockCycles(self.dut.clk, 3)
        self.dut.rst.value = 0
        await RisingEdge(self.dut.clk)

    def load(self, img, stalls, seed, fault=None):
        ram = self.ram
        ram.mem[:] = img.mem
        ram.stall = stalls
        ram.rng = random.Random(seed)
        if not stalls:
            ram.sig["gnt"].value = 1
        ram.arm(fault)

    async def run_image(self, img, stalls=False, seed=0, fault=None, reset=True, max_cycles=None,
                        extra_starts=0):
        """Run the chain of `img` once. extra_starts: write CTRL.START that many more times while
        BUSY (must be ignored)."""
        self.runs += 1
        if reset:
            await self.reset()
        self.load(img, stalls, seed, fault)
        m = self.m
        bound = max_cycles or (img.max_cycles * (3 if stalls else 1))
        await m.write(DESC, img.d_first)
        t0 = self.chk_s.cycle
        await m.write(CTRL, START)
        res = Result()
        while True:
            st, _ = await m.read(STATUS)
            if not st & BUSY:
                break
            if extra_starts and (self.chk_s.cycle - t0) > 0:
                extra_starts -= 1
                await m.write(CTRL, START)
            if self.chk_s.cycle - t0 > bound:
                res.timed_out = True
                break
        res.status = st
        res.cycles = (await m.read(CYCLES))[0]
        res.ncmd = (await m.read(NCMD))[0]
        res.err_addr = (await m.read(ERR_ADDR))[0]
        res.accesses = len(self.ram.log)
        got = bytes(self.ram.mem)
        res.got = got
        res.stamps = [int.from_bytes(got[img.off(a):img.off(a) + 4], "little") for a in img.stamp_addrs]
        a = np.frombuffer(got, dtype=np.uint8)
        e = np.frombuffer(bytes(img.expected), dtype=np.uint8).copy()
        for sa in img.stamp_addrs:
            o = img.off(sa)
            e[o:o + 4] = a[o:o + 4]
        bad = np.nonzero(a != e)[0]
        res.mismatches = int(bad.size)
        inside = np.zeros(RAM_SIZE, dtype=bool)
        for o, n in img.y_ranges():
            inside[o:o + n] = True
        res.y_mismatch = int(inside[bad].sum())
        res.outside = res.mismatches - res.y_mismatch
        if bad.size:
            self.dut._log.info("mismatching offsets (first 8): %s", [hex(int(b)) for b in bad[:8]])
        return res

    def check_normal(self, res, img, label):
        """Expectations of a fault-free run."""
        c = self.check
        c(not res.timed_out, f"{label}: no DONE within the cycle bound (hang)")
        c(res.status == DONE, f"{label}: STATUS {res.status:#x}, expected DONE only")
        c(res.ncmd == img.ncmds, f"{label}: NCMD {res.ncmd} != {img.ncmds}")
        c(res.y_mismatch == 0, f"{label}: {res.y_mismatch} y bytes differ from int8ops")
        c(res.outside == 0, f"{label}: {res.outside} bytes outside y changed (excl. STAMP)")
        c(all(s > 0 for s in res.stamps), f"{label}: STAMP words not written: {res.stamps}")
        c(res.cycles >= sum(res.stamps), f"{label}: CYCLES {res.cycles} < sum(STAMP) {sum(res.stamps)}")
        c(int(self.dut.busy.value) == 0, f"{label}: busy output high after completion")

    async def run_layer(self, L, x, xa=0, ya=0, stalls=False, seed=0):
        """Single layer through the RTL; returns (y bytes, Result). For the whole-network test."""
        from accel.gen_layers import Item, build_image
        img = build_image([Item(L, x, xa, ya)], self.P, seed)
        res = await self.run_image(img, stalls=stalls, seed=seed)
        p = img.placed[0]
        o = img.off(p.y_addr)
        return res.got[o:o + p.ny], res, img
