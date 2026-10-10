"""Python RAM slave model for cocotb (DESIGN section 3 slave side).

zero-wait: gnt = 1, response latency 1. stalling (seeded): gnt random with p = 0.7 each cycle,
response latency uniform 1..4. Byte-enable writes; addresses outside [base, base+size) answer
err with no write. Memory is a bytearray; the access happens at acceptance (single port).
Sampling at FallingEdge, driving right after RisingEdge (see bus_checker.py).
"""
import random

import cocotb
from cocotb.triggers import FallingEdge, RisingEdge


class RamSlave:
    def __init__(self, clk, sig, rst=None, base=0x8000_0000, size=4096, stall=False, seed=1):
        self.clk, self.sig, self.rst = clk, sig, rst
        self.base, self.size = base, size
        self.mem = bytearray(size)
        self.stall = stall
        self.rng = random.Random(seed)
        self.reads = self.writes = self.errors = 0
        sig["gnt"].value = 0 if stall else 1
        sig["rvalid"].value = 0
        sig["rdata"].value = 0
        sig["err"].value = 0
        self.task = None

    def start(self):
        self.task = cocotb.start_soon(self._run())
        return self.task

    def word(self, addr):
        o = addr - self.base
        return int.from_bytes(self.mem[o:o + 4], "little")

    def _access(self, addr, we, be, wdata):
        o = addr - self.base
        if not 0 <= o <= self.size - 4:
            self.errors += 1
            return 0, 1
        if we:
            for b in range(4):
                if be >> b & 1:
                    self.mem[o + b] = wdata >> (8 * b) & 0xFF
            self.writes += 1
            return 0, 0
        self.reads += 1
        return int.from_bytes(self.mem[o:o + 4], "little"), 0

    async def _run(self):
        s = self.sig
        pending = None  # [cycles_left, rdata, err]
        while True:
            await FallingEdge(self.clk)
            in_rst = self.rst is not None and int(self.rst.value) == 1
            if in_rst:
                pending = None
            elif int(s["req"].value) and int(s["gnt"].value):
                rdata, err = self._access(int(s["addr"].value), int(s["we"].value),
                                          int(s["be"].value), int(s["wdata"].value))
                lat = self.rng.randint(1, 4) if self.stall else 1
                pending = [lat, rdata, err]
            await RisingEdge(self.clk)
            if pending is not None:
                pending[0] -= 1
            if pending is not None and pending[0] == 0 and not in_rst:
                s["rvalid"].value, s["rdata"].value, s["err"].value = 1, pending[1], pending[2]
                pending = None
            else:
                s["rvalid"].value = 0
                s["rdata"].value = self.rng.getrandbits(32)  # garbage when not valid
                s["err"].value = self.rng.getrandbits(1)
            if self.stall:
                s["gnt"].value = int(self.rng.random() < 0.7)
