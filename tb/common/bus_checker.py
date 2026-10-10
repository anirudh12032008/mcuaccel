"""Per-cycle checker for the corv32 bus protocol (docs/DESIGN.md section 3, rules 1-8).

Sampling convention for all agents in tb/common: sample at FallingEdge (everything settled,
Verilator reads after RisingEdge may already show post-edge register values), drive right after
RisingEdge. One BusChecker watches one port; it sees master and slave signals alike.

Rule 4's "rvalid/rdata/err never depend combinationally on req" and rule 5 are structural, not
observable from a cycle sample, and are not checked here.
"""
import cocotb
from cocotb.triggers import FallingEdge

FIELDS = ("req", "addr", "we", "be", "wdata", "gnt", "rvalid", "rdata", "err")


def port(dut, prefix):
    """Signal dict for DESIGN-style ports named <prefix>req, <prefix>addr, ..."""
    return {f: getattr(dut, prefix + f) for f in FIELDS}


class BusChecker:
    def __init__(self, clk, sig, rst=None, name="bus", max_latency=64):
        self.clk, self.sig, self.rst, self.name = clk, sig, rst, name
        self.max_latency = max_latency
        self.cycle = 0
        self.accepted = 0
        self.responses = 0
        self.outstanding = False
        self._age = 0
        self._prev = None  # (req, gnt, addr, we, be, wdata) of the previous cycle
        self._prev_rst = False
        self.task = None

    def start(self):
        self.task = cocotb.start_soon(self._run())
        return self.task

    def fail(self, rule, msg):
        raise AssertionError(f"[{self.name}] cycle {self.cycle}: bus rule {rule} violated: {msg}")

    async def _run(self):
        while True:
            await FallingEdge(self.clk)
            self.cycle += 1
            self.sample()

    def sample(self):
        s = {f: int(self.sig[f].value) for f in FIELDS}
        in_rst = self.rst is not None and int(self.rst.value) == 1
        if in_rst:
            if self._prev_rst:  # registers have had a clock edge to reset
                if s["req"]:
                    self.fail(8, "req high during reset")
                if s["rvalid"]:
                    self.fail(8, "rvalid high during reset")
            self._prev_rst, self._prev, self.outstanding, self._age = True, None, False, 0
            return
        self._prev_rst = False
        # rule 2: hold until accepted
        if self._prev is not None:
            preq, pgnt, *pfields = self._prev
            if preq and not pgnt:
                if not s["req"]:
                    self.fail(2, "req withdrawn before acceptance")
                if [s["addr"], s["we"], s["be"], s["wdata"]] != pfields:
                    self.fail(2, "addr/we/be/wdata changed while waiting for gnt")
        if s["req"] and s["addr"] & 3:
            self.fail(1, f"unaligned address 0x{s['addr']:08x}")
        # rule 4: a response needs an earlier accepted request (strictly earlier cycle)
        if s["rvalid"]:
            if not self.outstanding:
                self.fail(4, "rvalid without an outstanding request (or in the acceptance cycle)")
            self.outstanding, self._age = False, 0
            self.responses += 1
        # rule 3: one outstanding
        if s["req"] and self.outstanding:
            self.fail(3, "req raised while a request is outstanding and no response this cycle")
        if self.outstanding:
            self._age += 1
            if self._age > self.max_latency:
                self.fail(4, f"no response within {self.max_latency} cycles")
        if s["req"] and s["gnt"]:
            self.outstanding, self._age = True, 0
            self.accepted += 1
        self._prev = (s["req"], s["gnt"], s["addr"], s["we"], s["be"], s["wdata"])

    def check_drained(self):
        """Call at end of test: every accepted request got its response."""
        if self.outstanding:
            self.fail(4, "test ended with an outstanding request")
