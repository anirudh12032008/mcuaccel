"""Simple cocotb bus master (one outstanding request, DESIGN section 3).

Call methods right after a RisingEdge; they return right after a RisingEdge.
"""
from cocotb.triggers import FallingEdge, RisingEdge


class BusMaster:
    def __init__(self, clk, sig):
        self.clk, self.sig = clk, sig
        sig["req"].value = 0
        for f in ("addr", "we", "be", "wdata"):
            sig[f].value = 0

    async def run(self, ops, back_to_back=False):
        """ops: list of (we, addr, wdata, be). Returns [(rdata, err)] per op, in order.
        back_to_back raises the next req in the response cycle (needs a latency-1 slave)."""
        s, results, i, cur, outst = self.sig, [], 0, None, 0
        while len(results) < len(ops):
            if cur is None and i < len(ops) and (outst == 0 or back_to_back):
                cur, i = ops[i], i + 1
            if cur is not None:
                we, addr, wdata, be = cur
                s["req"].value, s["we"].value, s["addr"].value = 1, we, addr
                s["wdata"].value, s["be"].value = wdata, be
            else:
                s["req"].value = 0
            await FallingEdge(self.clk)
            if int(s["rvalid"].value):
                results.append((int(s["rdata"].value), int(s["err"].value)))
                outst -= 1
            if cur is not None and int(s["gnt"].value):
                cur, outst = None, outst + 1
            await RisingEdge(self.clk)
        s["req"].value = 0
        return results

    async def access(self, we, addr, wdata=0, be=0xF):
        return (await self.run([(we, addr, wdata, be)]))[0]

    async def read(self, addr):
        return await self.access(0, addr, 0, 0xF)

    async def write(self, addr, wdata, be=0xF):
        return await self.access(1, addr, wdata, be)


async def clock_reset(dut, cycles=3):
    """Start a 10 ns clock, hold rst for `cycles` clocks; returns right after a RisingEdge."""
    from cocotb.clock import Clock
    from cocotb.triggers import ClockCycles
    import cocotb
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    await ClockCycles(dut.clk, cycles)
    dut.rst.value = 0
    await RisingEdge(dut.clk)
