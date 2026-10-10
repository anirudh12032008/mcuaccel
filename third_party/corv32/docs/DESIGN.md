# DESIGN — corv32

Status: v1 (Architect, 2026-10-04). This document is normative for implementers: signal
names, widths, encodings and timing below are the contract between modules built in
parallel. Change an interface only through docs/DECISIONS.md plus an edit here.
docs/SPEC.md defines *what* (ISA scope, claims, acceptance criteria); this file defines *how*.

## 0. Conventions

- RTL style follows CLAUDE.md: `logic`, `always_ff @(posedge clk)`, `always_comb`,
  `localparam`, packed vectors; no interfaces, no structs in ports, no `unique case`,
  no packages. Shared constants live in `rtl/core/corv32_defs.svh` (only `localparam`s),
  `` `include ``d *inside* module bodies. One module per file, file name = module name.
- Reset: synchronous, active-high `rst`, single clock `clk`. Only control state is reset
  (valids, PCs, CSRs, FSMs); datapath registers and the register file are not.
- "Cycle t" = the interval between rising edges; a register "loads at the end of t".
- `CORV32_RVFI` (define) enables RVFI ports and logic; defined in formal and all Verilator
  builds, undefined in synthesis. All RVFI-only logic sits inside `` `ifdef CORV32_RVFI ``
  blocks (this matters for the mutation study, §9.6).
- Addresses on both buses are byte addresses with `addr[1:0] == 2'b00`; byte lanes are
  selected by `be`. Data is little-endian: byte at address `4k+i` is on lane `i`
  (`data[8i+7:8i]`).

## 1. Block diagram

```
                         corv32_core (5-stage)
   +------------------------------------------------------------------------------+
   |  F               D                  EX                    MEM          WB     |
   | +----------+   +--------+   +----------------------+   +----------+  +-----+ |
   | |corv32_   |-->| decode |-->| fwd muxes, ALU,      |-->| load     |->| RF  | |
   | | fetch    |   | RF read|   | branch cmp + target, |   | extract, |  |write| |
   | | pc_f,    |   | hazard |   | CSR rd/wr, trap/irq, |   | mul hi/lo|  |RVFI | |
   | | 2-entry  |   | detect |   | dbus REQUEST, mul    |   | bus fault|  |     | |
   | | buffer   |   +--------+   | operand reg, div iter|   | trap     |  +-----+ |
   | +----------+       ^        +----------------------+   +----------+     |    |
   |   ^   |  ^         |  rd1/rd2      ^  ^   |  redirect      |  ^         |    |
   |   |   |  +---------+---------------+--+---+----------------+  |         |    |
   |   |   |   redirect (EX: branch/jump/trap/mret/fence.i; MEM: bus fault)   |    |
   |   |   |                  forwarding: MEM.result -> EX, WB.wdata -> EX    |    |
   |   |   |                  regfile write-through: WB -> D                  |    |
   +---|---|------------------------------------------------------|----------|----+
       |   v ibus (req/gnt, rvalid)            dbus (req/gnt, rvalid)         v rvfi_*
       |  +----------------------------------------------------------------+
       |  |                     corv32_interconnect                        |
       |  +----------------------------------------------------------------+
       |      | ibus side            | dbus side
       |   +--+------+-----+    +----+---+-----+------+------+---------+
       |   |bootrom  | RAM |    |bootrom | RAM | CLINT| UART | GPIO | SPI flash XIP
       |   |port A   |portA|    |port B  |portB|      |16550-|      | (wait states)
       |   +---------+-----+    +--------+-----+--+---+subset+------+----+----+
       |                                          |       |                |
       +--- irq_timer, irq_software, mtime -------+       | irq_external   v spi pins
                                               corv32_soc
```

## 2. Files and modules

| File | Responsibility |
|---|---|
| `rtl/core/corv32_defs.svh` | localparams: ALU ops, result selects, CSR addresses, mcause codes |
| `rtl/core/corv32_alu.sv` | combinational ALU (10 ops) + branch condition |
| `rtl/core/corv32_decode.sv` | combinational decoder: legality, fields, control bundle |
| `rtl/core/corv32_regfile.sv` | 32×32, 2 async read, 1 sync write, x0 = 0, optional write-through |
| `rtl/core/corv32_lsu.sv` | combinational: byte enables, store lane replication, misalign check, load extract/extend |
| `rtl/core/corv32_mul.sv` | MUL/MULH/MULHSU/MULHU; FAST=1 (DSP, result next cycle) or FAST=0 (iterative) |
| `rtl/core/corv32_div.sv` | DIV/DIVU/REM/REMU, radix-2 iterative, constant latency, WIDTH-parameterised |
| `rtl/core/corv32_csr.sv` | M-mode CSR file, counters, trap entry/return, interrupt pending/priority |
| `rtl/core/corv32_fetch.sv` | PC, ibus master, 2-entry fetch buffer, redirect/drop logic |
| `rtl/core/corv32_core.sv` | 5-stage pipeline top: D/EX/MEM/WB, hazards, forwarding, traps, RVFI |
| `rtl/core/corv32_sc.sv` | M1 single-cycle RV32I core (combinational memories), shares alu/decode/regfile/lsu |
| `rtl/soc/corv32_soc.sv` | SoC top: core + interconnect + memories + peripherals |
| `rtl/soc/corv32_interconnect.sv` | ibus/dbus address decode, response mux, error responder |
| `rtl/soc/corv32_ram.sv` | true-dual-port RAM with byte enables (BRAM-inferable) |
| `rtl/soc/corv32_bootrom.sv` | dual-read-port ROM, `$readmemh` init |
| `rtl/soc/corv32_clint.sv` | msip, mtimecmp, mtime (SiFive/Spike CLINT layout) |
| `rtl/soc/corv32_uart.sv` | 16550-subset UART (THR/RBR, IER, LSR), 8N1, fixed divisor |
| `rtl/soc/corv32_gpio.sv` | 32-bit out/in/oe |
| `rtl/soc/corv32_spiflash.sv` | memory-mapped read-only SPI flash (cmd 0x03), XIP window |
| `tb/` | cocotb tests (`tb/unit/`, `tb/core/`, `tb/sc/`), `tb/soc/` Verilator C++ harness, `spiflash_model.sv`, `tb/baselines/` harnesses |
| `formal/` | riscv-formal glue: `checks.cfg`, `wrapper.sv`, unit proofs `div8.sby`, `mul8.sby` |
| `verif/riscof/` | `config.ini`, `corv32/` DUT plugin + ISA/platform YAML, `spike/` reference plugin |
| `verif/lockstep/` | `gen.py` random program generator, `run.py`, `compare.py` |
| `verif/coverage/`, `verif/mutation/` | coverage merge + functional bins; mutant generation, bug patches, runner |
| `sw/` | `common/` (crt0.S, link.ld, uart), `bootrom/`, `tests/` (directed asm), `coremark/` (portme), `mcugrad/`, `tools/` (elf2hex, mkflash) |
| `syn/` | Yosys/nextpnr scripts, per-design register wrappers, ULX3S top + `.lpf` |

## 3. Bus protocol (ibus and dbus)

A simplified OBI-style protocol: an address phase handshake (`req`/`gnt`) and a response
phase (`rvalid`), with **at most one outstanding request per bus**.

Master → slave: `req`, `addr[31:0]` (word-aligned), and on dbus only `we`, `be[3:0]`,
`wdata[31:0]`. Slave → master: `gnt`, `rvalid`, `rdata[31:0]`, `err`. ibus has no
`we/be/wdata` (implicitly read, `be = 4'b1111`).

Rules (normative):
1. **Address phase.** A request is *accepted* at the end of a cycle in which
   `req && gnt`. `gnt` may depend combinationally on `req`/`addr`/`we` of the same cycle.
2. **Hold.** Once the master raises `req`, it keeps `req` high and `addr/we/be/wdata`
   stable until the request is accepted (no withdrawal).
3. **One outstanding.** The master may raise `req` in cycle t only if no accepted request
   is still awaiting its response, **or** that response is delivered in cycle t
   (`rvalid` = 1 in t). Hence at most one request is in flight at any clock edge, and a
   one-cycle-latency slave sustains one transfer per cycle.
4. **Response.** Exactly one response per accepted request, in order, in a cycle strictly
   after the acceptance cycle: `rvalid` high for exactly one cycle, with `rdata` (reads)
   and `err`. Writes are also acknowledged with `rvalid` (`rdata` don't-care).
   `rvalid/rdata/err` never depend combinationally on the same cycle's `req`.
5. **No back-pressure on responses.** The master always accepts `rvalid`.
6. **Errors.** `rvalid && err`: the access had no effect (no write performed) and read
   data is invalid; the core raises an access fault.
7. **Write visibility.** A write is visible to any request (either bus) accepted in or
   after the cycle in which its `rvalid` is asserted.
8. **Reset.** While `rst`, master drives `req = 0`; slaves drive `rvalid = 0`.

Zero-wait synchronous BRAM slave: `gnt = 1`; `rvalid <= req` (registered); `rdata` from the
BRAM output register → 1 access/cycle, 1 cycle latency.

```
Zero-wait slave, back-to-back fetch (one instruction per cycle):
 cycle      0      1      2      3      4
 req        1      1      1      1      0
 addr       A0     A1     A2     A3     -
 gnt        1      1      1      1      1
 rvalid     0      1      1      1      1
 rdata      -      D0     D1     D2     D3

Wait-state slave (e.g. SPI flash, ~130 cycles), gnt delayed by 1:
 cycle      0      1      2   ...   n      n+1
 req        1      1      0          1      0
 addr       A      A      -          B      -
 gnt        0      1      0          0/1    -
 rvalid     0      0      0          1      0
 rdata/err  -      -      -          D/e    -
 (A is held until accepted at the end of cycle 1; its response arrives in cycle n; the
  next request B may be raised in cycle n at the earliest, per rule 3)
```

## 4. Core top-level ports

### 4.1 `corv32_core` (five-stage; used by SoC, formal, synthesis)

```systemverilog
module corv32_core #(
  parameter logic [31:0] RESET_ADDR   = 32'h8000_0000,
  parameter bit          FORWARDING   = 1'b1, // 0: interlock-only ablation
  parameter bit          MUL_FAST     = 1'b1, // 1: DSP multiplier, result in MEM; 0: iterative in EX
  parameter bit          REDIRECT_REG = 1'b0  // 1: redirects registered (+1 cycle penalty) ablation
) (
  input  logic        clk,
  input  logic        rst,
  // instruction bus (master)
  output logic        ibus_req,
  output logic [31:0] ibus_addr,
  input  logic        ibus_gnt,
  input  logic        ibus_rvalid,
  input  logic [31:0] ibus_rdata,
  input  logic        ibus_err,
  // data bus (master)
  output logic        dbus_req,
  output logic [31:0] dbus_addr,
  output logic        dbus_we,
  output logic [3:0]  dbus_be,
  output logic [31:0] dbus_wdata,
  input  logic        dbus_gnt,
  input  logic        dbus_rvalid,
  input  logic [31:0] dbus_rdata,
  input  logic        dbus_err,
  // interrupts (level, synchronous to clk) and time base for the time/timeh CSRs
  input  logic        irq_external,
  input  logic        irq_timer,
  input  logic        irq_software,
  input  logic [63:0] mtime
`ifdef CORV32_RVFI
  , /* RVFI ports, §5 */
`endif
);
```

### 4.2 `corv32_sc` (M1 single-cycle RV32I)

```systemverilog
module corv32_sc #(parameter logic [31:0] RESET_ADDR = 32'h8000_0000) (
  input  logic        clk, rst,
  output logic [31:0] imem_addr,   // = pc (word-aligned)
  input  logic [31:0] imem_rdata,  // COMBINATIONAL: word at imem_addr, same cycle
  output logic        dmem_valid,  // load or store this cycle
  output logic [31:0] dmem_addr,   // word-aligned
  output logic        dmem_we,
  output logic [3:0]  dmem_be,
  output logic [31:0] dmem_wdata,  // lane-replicated store data
  input  logic [31:0] dmem_rdata,  // COMBINATIONAL: word at dmem_addr, same cycle
  output logic        halted       // sticky; set after ECALL/EBREAK/illegal/misaligned
`ifdef CORV32_RVFI
  , /* RVFI ports §5 without rvfi_csr_* */
`endif
);
```
One instruction per cycle; register file and stores update at the end of the cycle.
`corv32_regfile #(.WRITE_THROUGH(0))` (write-through would form a combinational loop),
`corv32_decode #(.HAS_M(0), .HAS_ZICSR(0))`. ECALL/EBREAK retire with `rvfi_halt=1,
rvfi_trap=0`; illegal / misaligned load/store/jump-target retire with `rvfi_trap=1,
rvfi_halt=1`, rd not written, no store. RVFI is registered (record for cycle t appears in
t+1). The test memory is `tb/sc/tb_sc.sv` (async-read array, `$readmemh`), not part of rtl/.

## 5. RVFI port (riscv-formal names; NRET=1, XLEN=32, ILEN=32)

Present when `CORV32_RVFI` is defined; driven from the WB stage registers (i.e. registered).

```
output logic        rvfi_valid       output logic [63:0] rvfi_order
output logic [31:0] rvfi_insn        output logic        rvfi_trap
output logic        rvfi_halt (=0)   output logic        rvfi_intr
output logic [1:0]  rvfi_mode (=3)   output logic [1:0]  rvfi_ixl (=1)
output logic [4:0]  rvfi_rs1_addr    output logic [4:0]  rvfi_rs2_addr
output logic [31:0] rvfi_rs1_rdata   output logic [31:0] rvfi_rs2_rdata
output logic [4:0]  rvfi_rd_addr     output logic [31:0] rvfi_rd_wdata
output logic [31:0] rvfi_pc_rdata    output logic [31:0] rvfi_pc_wdata
output logic [31:0] rvfi_mem_addr    output logic [3:0]  rvfi_mem_rmask
output logic [3:0]  rvfi_mem_wmask   output logic [31:0] rvfi_mem_rdata
output logic [31:0] rvfi_mem_wdata
// CSRs, each with _rmask/_wmask/_rdata/_wdata, 32 bits:
//   rvfi_csr_{mstatus,misa,mie,mtvec,mscratch,mepc,mcause,mtval,mip,
//             mvendorid,marchid,mimpid,mhartid,mconfigptr}_{rmask,wmask,rdata,wdata}
// 64 bits (riscv-formal counter convention):
//   rvfi_csr_{mcycle,minstret}_{rmask,wmask,rdata,wdata} [63:0]
```

Semantics:
- `rvfi_order`: increments by one per `rvfi_valid`, starting at 0 after reset.
- `rvfi_mem_addr` is word-aligned (`RISCV_FORMAL_ALIGNED_MEM`); `rmask`/`wmask` = `be`;
  `mem_rdata` = raw bus word; `mem_wdata` = lane-replicated store data.
- `rs1_addr/rs2_addr` are 0 for an unused operand (decoder guarantees, §6.2); `rs*_rdata`
  are the (forwarded) operand values the instruction executed with, 0 for x0.
- `rd_addr = rd_wdata = 0` when no register is written (including all trapping records).
- Exceptions: the faulting instruction retires with `rvfi_trap=1`, no rd/mem effects,
  `pc_wdata` = trap handler address. Interrupts: the interrupted instruction does not
  retire. In both cases (exception and interrupt) the first instruction of the handler has
  `rvfi_intr=1`; the Spike lockstep checks this after every trap entry.
- CSR fields are non-zero only for a Zicsr instruction addressing that CSR: `rmask` = all
  ones of the accessed half (always, even when rd = x0), `wmask` = all ones of the accessed
  half if a write is attempted, `rdata` = value before, `wdata` = value after. For
  `mcycle(h)`, `minstret(h)`, `cycle(h)`, `instret(h)` the 64-bit ports carry the full
  64-bit before/after values with masks on the accessed half. Trap entry/MRET CSR updates
  are not reported. `mepc` is reported unmasked (bit 1 visible).
- The core implements the generic carry (one `{addr, rmask64, wmask64, rdata64, wdata64}`
  per instruction down the pipe) and fans it out to the named ports in WB by address.

## 6. Shared submodule interfaces

### 6.1 `corv32_alu` (combinational)

```systemverilog
module corv32_alu (
  input  logic [31:0] a, b,
  input  logic [3:0]  op,        // see encoding
  input  logic [2:0]  br_funct3, // branch condition (instr funct3)
  output logic [31:0] result,
  output logic        br_taken   // condition(a, b) per br_funct3
);
```
`op = {alt, funct3}` so R-type decode is direct:

| op | name | result |
|---|---|---|
| 4'b0000 | ADD | a + b |
| 4'b1000 | SUB | a − b |
| 4'b0001 | SLL | a << b[4:0] |
| 4'b0010 | SLT | signed a < b |
| 4'b0011 | SLTU | unsigned a < b |
| 4'b0100 | XOR | a ^ b |
| 4'b0101 | SRL | a >> b[4:0] |
| 4'b1101 | SRA | a >>> b[4:0] |
| 4'b0110 | OR | a \| b |
| 4'b0111 | AND | a & b |
| other | — | 0 |

`br_taken`: 000 a==b, 001 a!=b, 100 a<b signed, 101 a≥b signed, 110 a<b unsigned,
111 a≥b unsigned, 010/011 → 0.

### 6.2 `corv32_decode` (combinational)

```systemverilog
module corv32_decode #(parameter bit HAS_M = 1'b1, parameter bit HAS_ZICSR = 1'b1) (
  input  logic [31:0] instr,
  output logic        illegal,
  output logic [4:0]  rs1_addr,    // 0 if rs1 not read (incl. CSR*I, LUI, AUIPC, JAL)
  output logic [4:0]  rs2_addr,    // 0 if rs2 not read (only R-type, branches, stores read it)
  output logic [4:0]  rd_addr,     // 0 if rd not written (branches, stores, system, fence)
  output logic [31:0] imm,         // I/S/B/U/J immediate per format, sign-extended
  output logic [3:0]  alu_op,      // §6.1; I-type uses {instr[30] for SRAI only, funct3}
  output logic [1:0]  alu_a_sel,   // 0 rs1, 1 pc (AUIPC), 2 zero (LUI)
  output logic        alu_b_imm,   // 1: b = imm, 0: b = rs2
  output logic [2:0]  res_sel,     // 0 ALU, 1 PC+4 (JAL/JALR), 2 LOAD, 3 CSR, 4 MULDIV
  output logic        is_branch, is_jal, is_jalr,
  output logic        is_load, is_store,
  output logic [1:0]  mem_size,    // 0 byte, 1 half, 2 word
  output logic        mem_unsigned,// LBU/LHU
  output logic        is_mul,      // MUL, MULH, MULHSU, MULHU
  output logic        is_div,      // DIV, DIVU, REM, REMU
  output logic [2:0]  funct3,      // raw instr[14:12]
  output logic        is_csr,
  output logic [11:0] csr_addr,    // instr[31:20]
  output logic        csr_imm,     // funct3[2]
  output logic        csr_wr,      // write attempted: funct3[1:0]==01, or instr[19:15]!=0
  output logic [4:0]  zimm,        // instr[19:15]
  output logic        is_ecall, is_ebreak, is_mret, is_wfi, is_fence, is_fencei
);
```
Legal set (everything else `illegal=1`, and then all `is_*`=0, rs/rd addrs = 0):
`instr[1:0]==2'b11` and LUI 0110111; AUIPC 0010111; JAL 1101111; JALR 1100111 f3=000;
BRANCH 1100011 f3∉{010,011}; LOAD 0000011 f3∈{000,001,010,100,101}; STORE 0100011
f3∈{000,001,010}; OP-IMM 0010011 (SLLI/SRLI need funct7=0000000, SRAI funct7=0100000);
OP 0110011 funct7=0000000 (all f3), 0100000 (f3 000, 101), 0000001 (if HAS_M);
MISC-MEM 0001111 f3=000 (FENCE, other fields ignored) or f3=001 (FENCE.I);
SYSTEM 1110011: exactly 0x00000073 ECALL, 0x00100073 EBREAK, and if HAS_ZICSR:
0x30200073 MRET, 0x10500073 WFI, f3∈{001,010,011,101,110,111} CSR*.
CSR address legality is checked by `corv32_csr`, not the decoder.

### 6.3 `corv32_regfile`

```systemverilog
module corv32_regfile #(parameter bit WRITE_THROUGH = 1'b1) (
  input  logic        clk,
  input  logic [4:0]  ra1, output logic [31:0] rd1,
  input  logic [4:0]  ra2, output logic [31:0] rd2,
  input  logic        we,  input  logic [4:0]  wa, input logic [31:0] wd
);
// rdN = (raN == 0) ? 0 : (WRITE_THROUGH && we && wa == raN) ? wd : mem[raN];
// write at clk edge when we && wa != 0. Async read; no reset. Infers LUTRAM on ECP5.
```

### 6.4 `corv32_lsu` (combinational)

```systemverilog
module corv32_lsu (
  input  logic [31:0] addr,          // effective byte address (EX)
  input  logic [1:0]  size,          // 0 B, 1 H, 2 W
  input  logic [31:0] store_data,    // rs2 value
  output logic        misaligned,    // (H && addr[0]) || (W && addr[1:0]!=0)
  output logic [3:0]  be,            // B: 4'b0001<<addr[1:0]; H: 4'b0011<<{addr[1],1'b0}; W: 4'b1111
  output logic [31:0] wdata_lanes,   // B: {4{d[7:0]}}, H: {2{d[15:0]}}, W: d
  input  logic [1:0]  ld_addr_lo,    // MEM: addr[1:0] of the load
  input  logic [1:0]  ld_size,
  input  logic        ld_unsigned,
  input  logic [31:0] ld_rdata,      // bus word
  output logic [31:0] ld_value       // selected lane(s), sign/zero-extended
);
```

### 6.5 `corv32_mul`

```systemverilog
module corv32_mul #(parameter bit FAST = 1'b1, parameter int WIDTH = 32) (
  input  logic             clk, rst,
  input  logic             req,   // EX holds a valid, not-killed MUL* op
  input  logic             adv,   // EX advances to MEM this cycle
  input  logic             kill,  // flush: abandon any iteration
  input  logic [1:0]       op,    // funct3[1:0]: 00 MUL, 01 MULH, 10 MULHSU, 11 MULHU
  input  logic [WIDTH-1:0] a, b,  // forwarded rs1, rs2
  output logic             done,  // FAST=1: constant 1. FAST=0: result valid in EX
  output logic [WIDTH-1:0] result
);
```
- FAST=1: operand/op registers load when `req && adv`; `result` is a combinational
  (WIDTH+1)×(WIDTH+1) signed product of those registers, upper/lower half selected → valid
  while that MUL is in MEM (held until the next `req && adv`). Mapped to MULT18X18D.
  Latency class "late" (§7.3).
- FAST=0: shift-add, 1 bit/cycle. IDLE → on `req` latch operands → WIDTH iterations →
  sign fix → `done=1` with `result` held while `req && !adv`; back to IDLE on `adv` or
  `kill`. EX occupancy WIDTH+2 cycles. Result is "early" (forwardable from MEM).
- `` `ifdef RISCV_FORMAL_ALTOPS ``: result = riscv-formal alternative op (MUL/MULH/MULHU:
  (a+b)^mask; MULHSU: (a−b)^mask), same timing as the selected FAST mode.

### 6.6 `corv32_div`

```systemverilog
module corv32_div #(parameter int WIDTH = 32) (
  input  logic             clk, rst, req, adv, kill,
  input  logic [1:0]       op,    // funct3[1:0]: 00 DIV, 01 DIVU, 10 REM, 11 REMU
  input  logic [WIDTH-1:0] a, b,
  output logic             done,
  output logic [WIDTH-1:0] result
);
```
Radix-2 restoring on magnitudes, constant latency: `done` first asserted in the
(WIDTH+2)-th cycle of `req` (EX occupancy WIDTH+2 = 34 cycles), held like FAST=0 mul.
Special cases (spec): x/0 → DIV −1, DIVU 2^W−1, REM x, REMU x; INT_MIN/−1 → DIV INT_MIN,
REM 0. `ALTOPS`: result = (a−b)^mask per op, `done` in the 4th cycle.

### 6.7 `corv32_csr`

```systemverilog
module corv32_csr (
  input  logic        clk, rst,
  // Zicsr access from EX
  input  logic        acc_valid,    // CSR instr commits this cycle (EX advancing, not killed)
  input  logic [11:0] acc_addr,
  input  logic [1:0]  acc_op,       // funct3[1:0]: 01 RW, 10 RS, 11 RC
  input  logic        acc_wr,       // decode.csr_wr
  input  logic [31:0] acc_src,      // rs1 value, or {27'b0, zimm}
  output logic [31:0] acc_rdata,    // current value at acc_addr (comb)
  output logic [31:0] acc_wdata,    // value that would be written (comb; RVFI)
  output logic        acc_illegal,  // comb: unimplemented, or acc_wr && acc_addr[11:10]==2'b11
  // trap entry / return
  input  logic        trap_take,    // never together with acc_valid or mret_take
  input  logic [31:0] trap_cause,   // full mcause value
  input  logic [31:0] trap_epc,
  input  logic [31:0] trap_tval,
  output logic [31:0] trap_vector,  // comb: mtvec[0] && trap_cause[31] ? BASE + 4*cause[4:0] : BASE
  input  logic        mret_take,
  output logic [31:0] mepc_o,       // {mepc[31:2], 2'b00}
  // interrupts
  input  logic        irq_external, irq_timer, irq_software,  // raw; registered inside (mip_q)
  output logic        irq_pending,  // mstatus.MIE && |(mip_q & mie)
  output logic [31:0] irq_cause,    // 0x8000000B (MEI) > 0x80000003 (MSI) > 0x80000007 (MTI)
  // counters
  input  logic        instret_inc,  // an instruction committed at EX this cycle
  input  logic        instret_dec,  // a MEM bus fault this cycle (un-count that instruction)
  input  logic [63:0] mtime,
  output logic [63:0] mcycle_o, minstret_o   // for RVFI 64-bit counter fields
);
```
Edge behaviour: CSR write when `acc_valid && acc_wr && !acc_illegal` (RW: src; RS:
old|src; RC: old&~src; then the per-CSR write mask of SPEC §4.2). `mcycle` +1 every cycle
unless written that cycle. `minstret`: write wins; else +1 on `instret_inc`, −1 on
`instret_dec` (never both). `trap_take`: mepc←epc, mcause←cause, mtval←tval, MPIE←MIE,
MIE←0. `mret_take`: MIE←MPIE, MPIE←1. 64-bit halves: writing the low (high) half
replaces only that half.

### 6.8 `corv32_fetch`

```systemverilog
module corv32_fetch #(
  parameter logic [31:0] RESET_ADDR = 32'h8000_0000,
  parameter bit          REDIRECT_REG = 1'b0
) (
  input  logic        clk, rst,
  output logic        ibus_req,  output logic [31:0] ibus_addr,
  input  logic        ibus_gnt,  input  logic ibus_rvalid,
  input  logic [31:0] ibus_rdata, input logic ibus_err,
  // to D
  output logic        d_valid,   // an instruction is presented this cycle
  output logic [31:0] d_instr,
  output logic [31:0] d_pc,
  output logic        d_fault,   // ibus err on this fetch
  output logic        d_intr,    // first instruction after a trap redirect (RVFI)
  input  logic        d_ready,   // D consumes the presented instruction this cycle
  // redirect (squash everything presented, buffered or in flight; continue at redirect_pc)
  input  logic        redirect,
  input  logic [31:0] redirect_pc,
  input  logic        redirect_trap
);
```
State: `pc_f` (next address to request), `out` (1 accepted request awaiting response),
`out_pc`, `out_drop`, `held` (req raised, not yet granted), `buf[2]` of
{pc, instr, fault, intr} with count `bcnt`.
- Presented to D: `buf[0]` if `bcnt>0`; else the live response if
  `ibus_rvalid && out && !out_drop`; else nothing.
- A live response that is not consumed (`!d_ready`) is pushed to `buf`. Consumption pops.
- Issue rule (no combinational path from D's stall): `ibus_req = held ||
  (bcnt + out < 2 && (!out || ibus_rvalid))`; `ibus_addr = pc_f` (or the held address);
  on acceptance `pc_f += 4`, `out_pc` = issued address.
- Redirect in cycle t (REDIRECT_REG=0): `buf` cleared, live response discarded; if a
  response is still owed after t (`out && !ibus_rvalid`, or `held`), its response is
  marked `out_drop`. If a fresh request may be issued in t (rule 3, not `held`), it is
  issued for `redirect_pc` combinationally (`ibus_addr = redirect_pc`) and `pc_f <=
  redirect_pc + 4`; otherwise `pc_f <= redirect_pc`. `redirect_trap` makes the first
  instruction of the new stream carry `d_intr=1`.
- REDIRECT_REG=1: in t the squash happens, `redirect_pc` is captured; in t+1 the fetch
  behaves as an unregistered redirect to the captured pc (and squashes anything arriving
  in t+1).
- Result with zero-wait ibus: sustained 1 instr/cycle; stalls in D cost no extra bubble
  (the 2-entry buffer absorbs the one in-flight response).

## 7. Pipeline

### 7.1 Stages
- **F**: `corv32_fetch`. Fetch request in F, instruction returns in D's cycle.
- **D**: instruction from `corv32_fetch` (live BRAM output or buffer); decode; regfile read
  (write-through from WB); hazard detection; loads ID/EX.
- **EX**: forwarding muxes; ALU; branch condition; target adder `pc+imm` (branch/JAL),
  ALU `rs1+imm & ~1` (JALR); exception detection; interrupt decision; CSR read/modify/write
  (the CSR file is updated at the end of EX); **dbus request issued** (address from the
  ALU, `be/wdata` from `corv32_lsu`); mul operand capture (FAST) or mul/div iteration.
- **MEM**: wait for `dbus_rvalid`; load extract/extend; FAST mul result; bus-fault trap.
- **WB**: regfile write; RVFI outputs.

### 7.2 Stall/flow signals (all combinational, evaluated each cycle)
- `stall_mem = mem_valid && mem_is_ls && !dbus_rvalid` (MEM holds; bubble to WB).
- `ex_md_wait = ex_valid && ((is_div || (is_mul && !MUL_FAST)) && !done)`.
- `ex_bus_wait = dbus_req && !dbus_gnt`.
- `stall_ex = stall_mem || ex_md_wait || ex_bus_wait` (EX holds; bubble to MEM).
- `hazard_d` (§7.3); `stall_d = stall_ex || hazard_d` (D holds; bubble to EX if !stall_ex).
- `d_ready = d_valid && !stall_d`.
- While EX holds, the EX operand registers **re-capture the forwarded values every cycle**,
  so producers that pass through WB during the stall are not lost.

### 7.3 Forwarding and hazards
- Producers are "early" (result known at end of EX: ALU, LUI/AUIPC, JAL/JALR link, CSR,
  FAST=0 mul, div) or "late" (load; FAST=1 mul) — late results exist only at end of MEM.
- FORWARDING=1, operand select in EX for rs (rs≠0), first match wins:
  1. MEM stage: `mem_valid && mem_rd==rs && !mem_late` → `mem_result`;
  2. WB stage: `wb_valid && wb_rd==rs` → `wb_wdata`;
  3. ID/EX captured value (includes regfile write-through).
  Applies to ALU operands, branch compare, JALR base, store data, CSR source, mul/div operands.
- Load-use: `hazard_d = ex_valid && ex_late && ex_rd!=0 && (ex_rd==d_rs1 || ex_rd==d_rs2)`
  (d_rs* are 0 when unused). Penalty 1 cycle; the consumer then forwards from WB.
- FORWARDING=0: no muxes 1–2; `hazard_d` = any valid EX or MEM instruction with
  `rd!=0` matching d_rs1/d_rs2 (WB covered by write-through).

### 7.4 Branches and redirects
- All control transfers resolve in EX: conditional branches, JAL, JALR, MRET (→mepc),
  FENCE.I (→pc+4), exceptions/interrupts (→trap_vector). MEM bus faults redirect from MEM
  (priority over EX). Redirect fires only when the EX instruction leaves EX (`!stall_ex`)
  or is killed by a trap.
- **Prediction: static not-taken, no BTB.** Redirect drives `ibus_addr` in the same cycle
  (REDIRECT_REG=0), so a taken branch/jump squashes exactly one instruction (the one in D):
  **penalty 1 cycle**; not-taken: 0. REDIRECT_REG=1: penalty 2.
- Justification: §7.8 — the target is reachable without prediction; a predictor is the
  first mitigation if the Step-3 instruction count invalidates the estimate.

### 7.5 Exceptions, interrupts, commit
- Commit point: an instruction **commits when it leaves EX** without being killed
  (`ex_commit = ex_valid && !ex_exc && !irq_take && !mem_trap && !stall_ex`), except a
  load/store, which can still fault in MEM. Everything with side effects happens at or
  after commit: CSR writes, MRET/trap CSR updates, `instret_inc`, redirects, and the dbus
  request (which is issued in EX in the same cycle and gated by the same conditions).
- `dbus_req = held_ex || (ex_valid && (is_load||is_store) && !ex_exc && !stall_mem &&
  !mem_trap && !irq_take)` (`ex_exc` includes misalignment and fetch faults); `held_ex` keeps it high until `gnt` (rule 2). Interrupts are
  never taken on an EX instruction with `held_ex`.
- Exceptions detected by EX (`ex_exc`): fetch fault (from D), illegal (decode, or
  `acc_illegal` for CSR*), ECALL, EBREAK, load/store misaligned, taken-target misaligned.
  Taken when `ex_valid && ex_exc && !stall_mem && !mem_trap && !irq_take`: the instruction
  becomes a trap record into MEM (RVFI `trap=1`), `trap_take`, redirect to `trap_vector`.
- `mem_trap = mem_valid && mem_is_ls && dbus_rvalid && dbus_err`: MEM record becomes a trap
  record, EX is killed (no request, no CSR write), D/F squashed, `instret_dec`,
  `trap_take` with mepc = mem pc, cause 5/7, tval = mem effective address.
- Interrupts: `irq_take = irq_pending && ex_valid && !held_ex && !stall_mem && !mem_trap`
  (priority over the EX instruction's own exception). EX instruction discarded (not
  retired), mepc = its pc, cause = `irq_cause`. Mul/div iterations in EX are killed.
- Exception table:

| cause | code | detected | mtval |
|---|---|---|---|
| instr address misaligned | 0 | EX (taken target[1]) | target |
| instr access fault | 1 | fetch err → EX | pc |
| illegal instruction | 2 | D/CSR → EX | instruction bits |
| breakpoint | 3 | EX | pc |
| load / store misaligned | 4 / 6 | EX | effective address |
| load / store access fault | 5 / 7 | MEM | effective address |
| ECALL (M) | 11 | EX | 0 |
| MSI / MTI / MEI | 0x8000_0003 / 7 / B | EX | 0 |

### 7.6 RVFI production
Each pipeline register carries an `rvfi_*` bundle (inside `ifdef CORV32_RVFI`): insn,
pc_rdata, intr (from fetch), rs addrs, rs rdata (captured from the forwarded operands
when EX commits or traps), pc_wdata (EX: taken ? target : pc+4; MRET: mepc; trap:
trap_vector; overwritten in MEM on bus fault), mem addr/masks/wdata (EX), mem rdata (MEM),
trap, CSR generic fields (EX). WB presents the bundle; `rvfi_valid = wb_valid`.

### 7.7 Reset
`pc_f = RESET_ADDR`; all stage valids, `out`, `held`, `bcnt`, div/mul FSMs cleared; CSRs
per SPEC §4.2.

### 7.8 CPI estimate and branch-prediction choice (PREDICTIONS from a model, not measurements)
No corv32 RTL has been run on CoreMark. The instruction count and mix below are
**measured on Spike** for the SPEC §3 `-O2` binary (`results/coremark_mix/mix_O2.json`,
method in `results/coremark_mix/README.md`; generator `experiments/coremark_mix.py`). The CPI
and CoreMark/MHz figures are **predictions**: the penalties of §7.2-7.4 applied to that trace.
They are replaced by measured cycles once M2 runs CoreMark in RTL.

| Event | measured (fraction of instr.) | penalty (default) | predicted CPI add |
|---|---|---|---|
| taken conditional branches (18.5% branches, 55.3% of them taken) | 10.21% | 1 | 0.102 |
| JAL/JALR | 2.67% | 1 | 0.027 |
| load with immediate consumer (19.0% loads, 37.9% of them use-distance 1) | 7.19% | 1 | 0.072 |
| MUL with immediate consumer (3.26% MUL; **use-distance 1 for 100%** of them) | 3.26% | 1 | 0.033 |
| DIV (none in the timed region) | 0 | 33 | 0 |
| **CPI (predicted, default config)** | | | **1.233** |

The first draft of this section assumed 2% MUL with 30% at distance 1 (0.6% of instructions);
the measurement is 3.26% and 100%, which is why the MUL term is 0.033, not 0.006.

N = 288,230 instructions/iteration (mean of the 10->20 iteration difference; the 5->10 pair
gives 288,215). CoreMark/MHz = 10⁶ / (N × CPI). Predicted, default config
(FORWARDING=1, MUL_FAST=1, REDIRECT_REG=0): 355,480 cycles/iteration, **2.813 CoreMark/MHz**.
REDIRECT_REG=1: 392,619 cycles, 2.547. The target ≥ 2.0 holds in the model for
N × CPI ≤ 500k cycles. Step-3 gate (N × CPI > 450k cycles/iteration adds static BTFN in D):
355,480 < 450,000, so the gate does **not** trigger; no predictor is built (D11).
Ablation predictions (SPEC C3, same file): FORWARDING=0 → 1.903 (additive model) / 2.020
(trace-driven, which accounts for a redirect bubble removing a hazard); MUL_FAST=0 → 1.524,
below the 2.0 target. A dynamic predictor could save at most the 0.102 + 0.027 branch term.

Multiplier: DSP single-cycle (FAST=1) default because CoreMark's matrix kernel is
multiply-heavy and the iterative version costs ≈0.6 CPI; divider iterative (radix-2,
34 cycles) because CoreMark divides are rare and a fast divider is large.

## 8. SoC

### 8.1 Memory map

| Base | Size | Device | ibus | dbus |
|---|---|---|---|---|
| 0x0000_0000 | 4 KiB | boot ROM (`corv32_bootrom`) | R | R (W → err) |
| 0x0200_0000 | 64 KiB | CLINT | — | RW |
| 0x1000_0000 | 256 B | UART (16550 subset) | — | RW |
| 0x1000_1000 | 256 B | GPIO | — | RW |
| 0x2000_0000 | 16 MiB | SPI flash XIP (read-only, wait states) | — | R (W → err) |
| 0x8000_0000 | RAM_BYTES | RAM (`corv32_ram`); 64 KiB FPGA, 1 MiB Verilator | RX | RW |
| other | | error responder (gnt=1, rvalid+err next cycle) | err | err |

RAM and ROM decode exactly (no aliasing beyond their size). CLINT and UART addresses match
Spike's, so the same binaries print and take MSI/MTI on both. Unmapped test address used
for access faults: 0x4000_0000 (unmapped on Spike too).

### 8.2 Peripherals
- `corv32_clint #(PRESCALE=1)`: +0x0000 msip[0] → `irq_software`; +0x4000/+0x4004
  mtimecmp lo/hi (reset all ones); +0xBFF8/+0xBFFC mtime lo/hi (RW, reset 0, +1 every
  PRESCALE clocks); `irq_timer` = registered (mtime ≥ mtimecmp); `mtime` output to core.
  Other offsets read 0 / ignore writes. gnt=1, rvalid next cycle; honours `be`.
- `corv32_uart #(DIV)`: byte registers at +0 RBR (read pops; 0 if empty) / THR (write
  starts TX if idle, else dropped), +1 IER (bit0 = RX-data interrupt enable), +5 LSR (bit0
  DR, bit5 THRE, bit6 TEMT). 8N1, DIV clocks per bit (FPGA: clk/115200; Verilator: 8).
  `irq_external = IER[0] && DR`. 2-FF RX synchroniser, mid-bit sampling.
- `corv32_gpio`: +0 OUT (RW), +4 IN (RO, 2-FF sync), +8 OE (RW); reset 0.
- `corv32_spiflash`: read word at window offset o: CS low, SPI mode 0, SCK = clk/2,
  send 0x03 + o[23:0], read 4 bytes (first byte → `rdata[7:0]`), CS high, `rvalid`.
  ≈130 cycles. `gnt` only when idle. Writes → err.
- `corv32_interconnect`: decodes `addr` combinationally to one slave, forwards
  `req/gnt`, latches the slave index on acceptance, muxes `rvalid/rdata/err` from it.
- `corv32_ram #(BYTES, INIT_FILE)`: port A (ibus, read), port B (dbus, RW + be); both
  zero-wait slaves. Same-address A-read/B-write in one cycle returns undefined data on A
  (self-modifying code must FENCE.I). `ifndef SYNTHESIS`: `+ram=<hex>` plusarg preloads.
- `corv32_bootrom #(INIT_FILE="bootrom.hex")`: two read ports.

### 8.3 Boot (flash boot choice)
Choice: a real SPI protocol behind a memory-mapped XIP read controller, with a boot ROM
that copies the image to RAM. Reason: it is what a ULX3S (SPI flash) needs, it exercises
the wait-state side of the bus protocol, and the boot ROM stays ~20 instructions. Rejected:
"memory-mapped flash with N wait states" (not a real device) and bit-banged SPI via GPIO
(slow, more firmware). Executing from flash (XIP on ibus) is not supported (not needed).

Flash image (`sw/tools/mkflash.py`): word 0 magic 0x3256_5243 ("CRV2"), word 1 length in
bytes, word 2 load address, word 3 entry, then payload. Boot ROM (`sw/bootrom/bootrom.S`):
read magic at 0x2000_0000; if valid copy `length` bytes to load address, `fence.i`, jump
to entry; else jump to 0x8000_0000 (bench mode: RAM preloaded via `+ram`, flash empty
reads 0xFFFF_FFFF).

### 8.4 SoC top and Verilator model
```systemverilog
module corv32_soc #(
  parameter int    RAM_BYTES = 65536, parameter int UART_DIV = 217,
  parameter string BOOTROM_INIT = "bootrom.hex",
  parameter bit FORWARDING = 1, MUL_FAST = 1, REDIRECT_REG = 0
) (
  input  logic clk, rst,
  output logic uart_tx, input logic uart_rx,
  output logic [31:0] gpio_out, gpio_oe, input logic [31:0] gpio_in,
  output logic spi_cs_n, spi_sck, spi_mosi, input logic spi_miso
`ifdef CORV32_RVFI
  , /* core RVFI ports passed through */
`endif
);
```
Core `RESET_ADDR = 0`. Verilator model `build/sim/Vcorv32_soc` = `tb/soc/sim_main.cpp` +
`tb/soc/tb_soc.sv` (SoC + `spiflash_model.sv`, MISO=1 if no image). Plusargs: `+flash=`,
`+ram=`, `+tohost=<hex addr>` (stop when a retired store writes it: value 1 = pass, else
fail code), `+max_cycles=`, `+trace=<file>` (RVFI trace, one line per retired instr:
`order pc insn trap intr rd rd_wdata mem_addr rmask wmask mem_wdata mem_rdata`),
`+signature=<file> +sig_begin= +sig_end=` (dump RAM words, 8 hex digits per line),
`+uart_rx=<file>`, `+vcd`. UART output: harness prints bytes from retired byte stores to
0x1000_0000 (seen on RVFI). Built with `-DCORV32_RVFI`, `RAM_BYTES=1 MiB`, `UART_DIV=8`.

## 9. Verification plan

### 9.1 cocotb (Verilator backend, `make test`)
- Unit: `tb/unit/test_{alu,decode,regfile,lsu,mul,div,csr,fetch}.py` against Python
  reference models (`tb/model/rv32.py`), criteria in SPEC §5 M1/M3. `test_fetch` drives a
  random-latency ibus slave and random redirects; checks in-order delivery and drops.
- Core: `tb/core/` runs self-checking asm (`sw/tests/**`; pass = store 1 to `tohost`)
  on `corv32_core` with a Python OBI slave memory in zero-wait and random-wait modes;
  cycle-exact tests of SPEC M2.3; trap/interrupt/CSR directed tests (M3.2).
- `tb/sc/` runs the same asm on `corv32_sc`.

### 9.2 riscv-formal (`formal/checks.cfg`, run by `make formal`)
```
[options]
isa rv32im
nret 1
nbus 2
solver boolector

[depth]
insn            24
reg        12   24
pc_fwd     12   24
pc_bwd     12   24
unique      1  12 24
causal     12   24
liveness    1  12 30
ill             24
cover       1   20
csrw            24
csr_ill         24
csrc_inc    1   20
csrc_upcnt  1   20
csrc_const  1   20
csrc_any    1   20
csrc_zero   1   20
bus_imem    1   24
bus_dmem    1   24

[csrs]
mvendorid   const
marchid     const
mimpid      const
mhartid     const
mconfigptr  const
misa        const
mstatus     zero_mask=32'hFFFFE777
mie         zero_mask=32'hFFFFF777
mip         zero_mask=32'hFFFFF777
mtvec
mscratch    any
mepc
mcause
mtval
mcycle      inc upcnt
minstret    inc upcnt

[illegal_csrs]
302 m rw
303 m rw
306 m rw
30a m rw
31a m rw
7b0 m rw
c03 m rw
c00 m w
c01 m w
c02 m w
c80 m w
c81 m w
c82 m w
f11 m w
f12 m w
f13 m w
f14 m w
f15 m w

[defines]
`define RISCV_FORMAL_ALIGNED_MEM
`define RISCV_FORMAL_ALTOPS
`define CORV32_RVFI

[defines liveness]
`define CORV32_FAIRNESS

[verilog-files]
formal/wrapper.sv and every rtl/core/*.sv, listed explicitly with absolute paths
generated by formal/Makefile (sby does not expand globs)
```
(Exact cfg syntax, e.g. mask literal format, is validated against genchecks.py; the
*check set and depths* are the claim and may not be reduced without a SPEC change.)
Depth rationale: zero-wait retire latency from reset ≈ 6 cycles; the wrapper allows up to 4
stall cycles per handshake; ALTOPS div occupies EX 4 cycles; 24 lets several instructions
precede the checked one under stalls.

`formal/wrapper.sv` (`rvfi_wrapper`): instantiates `corv32_core` (default params,
`RESET_ADDR` arbitrary const); per bus a protocol-legal abstract slave from
`rvformal_rand_reg` values: random `gnt`; an outstanding flag; `rvalid` random but only
when a request is outstanding; random `rdata`; **`err` = 0** (SPEC §7.4); random
`irq_*` and `mtime`. `CORV32_FAIRNESS`: `gnt` within 4 cycles of `req`, `rvalid` within 4
cycles of acceptance. RVFI_BUS channel 0 (imem) / 1 (dmem) emitted on each response from
the accepted address/be/wdata and the response data (pattern of riscv-formal's nerv
wrapper; written from scratch).

Unit proofs (`formal/div8.sby`, `formal/mul8.sby`, mode prove): `corv32_div #(8)` and
`corv32_mul #(.FAST(0), .WIDTH(8))` result equals the spec function of the operands
latched at `req`, for all inputs.

### 9.3 RISCOF (`make riscof`)
`verif/riscof/config.ini`: DUT plugin `corv32`, reference plugin `spike`.
- ISA YAML: `ISA: RV32IMZicsr_Zifencei`, `hw_data_misaligned_support: false`, misa as SPEC.
- DUT compile: `riscv-none-elf-gcc -march=rv32im_zicsr_zifencei -mabi=ilp32 -static
  -mcmodel=medany -nostdlib -nostartfiles -T verif/riscof/corv32/env/link.ld` (0x8000_0000);
  `RVMODEL_HALT` stores 1 to `tohost`; run `Vcorv32_soc +ram= +tohost= +signature=
  +sig_begin= +sig_end=` (symbols from `riscv-none-elf-nm`), `+max_cycles=10000000`.
- Reference: `spike --isa=rv32im_zicsr_zifencei --priv=m +signature=<f>
  +signature-granularity=4 <elf>`.
- Pass: RISCOF report lists all selected tests (expected 63) as Passed.

### 9.4 Spike lockstep (`make lockstep`)
- Generator `verif/lockstep/gen.py --seed S` → `.S` (then ELF at 0x8000_0000, `tohost`/
  `fromhost` symbols). Program: init (mtvec ← handler, direct; all of x1..x31 random;
  4 KiB data window at 0x8008_0000 random; x30 = window base + 2048), body of ~6,000 random
  instructions, end (store 1 to tohost). Reserved: x26–x29 (handler/loop/jump scratch),
  x30 (data base). Handler: `csrr` mcause, mepc, mtval into x26–x28 (so they are compared
  via rd writes); init writes mtimecmp = all ones before anything else (Spike resets
  mtimecmp to 0, so MTIP is pending from reset; the corv32 CLINT resets to all ones, §8.2);
  interrupt → clear source (msip=0 / mtimecmp=all ones); exception →
  mepc += 4; ≥ 2 instructions before `mret`.
- Class weights: ALU R/I 45%; loads 15% and stores 8% (base x30, offsets ±2047, 5%
  misaligned, 1% via 0x4000_0000 → access fault); M 10% (operands biased to 0, ±1,
  INT_MIN, INT_MAX); branches 10% (forward ≤16 instrs, plus bounded backward loops on
  x29); JAL/JALR 3% (0.5% to target with bit1 set); CSR ops 3% on {mstatus (MIE/MPIE),
  mie, mscratch, mepc, mcause, mtval, mip(read), misa(read), mvendorid/mimpid/mhartid/
  mconfigptr(read), mstatush}; ECALL/EBREAK 2%; illegal words 1% (0, 0xFFFFFFFF, reserved
  opcodes legal on neither model); FENCE/WFI 0.5%; FENCE.I self-modifying pattern 0.2%;
  interrupt patterns 0.5% (MIE=0; set msip=1 or mtimecmp=0; ≥2 instrs; set MIE → taken
  immediately after the csr write on both models).
- Spike: `spike --isa=rv32im_zicsr_zicntr_zifencei --priv=m -m0x80000000:0x100000
  --wfi-as-nop -l --log-commits --log=<f> <elf>` (flag present in pinned
  `spike_main/spike.cc`; logs cached per seed).
- Compare (`compare.py`): skip records before the first pc 0x8000_0000 on both sides;
  then in order: pc, insn, rd write (x0 never), load address, store address+data(size);
  DUT `trap=1` records ↔ Spike exception lines; the trace has no mcause/mtval, so cause,
  epc and tval are compared through the handler's `csrr` rd writes; DUT `intr=1` exactly
  on the first record after any Spike trap entry (exception or interrupt). The instruction
  word of a Spike 16-bit-pattern illegal word is compared on its low half only (Spike's
  mtval is 16 bits there; SPEC §4.3). Stop at first mismatch with ±20 records of context.
- **Exclusion list (pre-declared; generator never emits these):** reads of
  cycle/time/instret/mcycle/minstret(+h), marchid, mhpmevent*, mcountinhibit, writes to
  misa/mtvec mode≠0, accesses to UART/GPIO/flash/boot ROM, CLINT other than msip/mtimecmp,
  external interrupts, bus wait states.
- Campaign: seeds 1..200; if < 1,000,000 compared, extend with contiguous seeds until
  ≥ 1,000,000. The seed range and count are recorded.

### 9.5 Coverage (`make coverage`)
Verilator `--coverage-line --coverage-toggle` builds of the cocotb core tests, RISCOF and
lockstep runs; merged with `verilator_coverage` → `results/coverage/` (per-file line/toggle,
annotated sources). Functional bins from RVFI traces (`verif/coverage/func.py`): every
RV32IM/Zicsr/system opcode retired; per opcode class producer→consumer distance 1/2/3 on
rs1 and rs2 (producer classes: ALU, load, mul, div, CSR, link); taken/not-taken per
branch type; every exception cause and interrupt cause; CSR op × CSR. Targets in SPEC §5 M4.

### 9.6 Mutation / planted bugs (pre-registered procedure for SPEC C1)
**Mutants.** `verif/mutation/gen.ys`:
`read_verilog -sv -DCORV32_RVFI -DRISCV_FORMAL_ALTOPS rtl/core/*.sv; prep -flatten -top
corv32_core; mutate -list 1000 -seed 1 -none > list.txt` (Yosys 0.69 default modes:
inv, const0, const1, cnot0, cnot1). Filter in list order: drop the `none` entry and every
entry whose `-src` lies inside a `` `ifdef CORV32_RVFI `` region (line ranges computed by
`verif/mutation/rvfi_regions.py`); take the **first 200** remaining. Each mutant is applied
with `mutate <entry>` and written as one flattened `corv32_core` netlist that all four
methods consume. The `none` mutant and the original design are run as controls and must
be killed by no method.
**Hand-written bugs** (patches `verif/mutation/bugs/NN-*.patch`, written and committed
before any evaluation):
1 forwarding priority reversed (WB over MEM); 2 load-use check ignores rs2; 3 taken branch
does not squash D; 4 forwarding from producer with rd=x0; 5 LB/LBU wrong lane for
addr[1:0]=3; 6 SH byte enables wrong for addr[1]=1; 7 SRAI behaves as SRLI; 8 SLTU
compares signed; 9 JALR does not clear bit 0; 10 MULHSU treats rs2 as signed; 11 DIV by 0
returns 0; 12 REM INT_MIN%−1 non-zero; 13 interrupt mepc = pc+4; 14 MIE not cleared on
trap entry; 15 MRET does not restore MIE; 16 misaligned LW not detected for addr[1:0]=2;
17 minstret counts interrupted instruction; 18 fetch `out_drop` cleared before the stale
response arrives (needs ibus wait states); 19 EX store issued in the cycle of a MEM bus
fault; 20 CSRRS with rs1=x0 treated as a write (illegal on read-only CSRs).
**Kill criteria.** M_dir: any core-level cocotb test fails or exceeds 10× nominal cycles.
M_act: any signature mismatch/timeout. M_fv: any §9.2 check FAILs (checks run in fixed
order, stop at first FAIL; per-check timeout 1 h = not killed). M_ls: any mismatch or
hang over the §9.4 campaign seeds (Spike logs cached). Every method runs on every bug
(complete matrix). Record CPU-seconds to first kill, and instructions or depth at kill.
**Survivors** are triaged in `results/mutation/triage.md` as Equivalent (with argument,
e.g. performance-only) or Missed; K = killed ∪ Missed.
**Compute guard (pre-registered).** If projected M_fv compute exceeds 400 CPU-hours,
evaluate the first 100 mutants of the list instead of 200 (all 20 hand-written bugs
always), and report that.

## 10. Synthesis and FPGA measurement (M6)

- Device: LFE5U-25F, speed grade 6, CABGA381 (ULX3S). Reason: matches the board the user
  can target; ECP5 is fully supported by Yosys/nextpnr/Trellis.
- Flow (identical for every design): `yosys -p "read_verilog -sv <files>; synth_ecp5 -top
  <wrap> -json x.json"`; `HOME=.cache/home nextpnr-ecp5 --25k --package CABGA381 --speed
  6 --out-of-context --freq 150 --timing-allow-fail --seed s --json x.json --report r.json`,
  seeds 1..10.
- Core-only wrapper `syn/wrap/<design>_wrap.sv`: one register on every core input and
  output (no logic), so all timed paths are FF→FF; out-of-context because core pin counts
  (>197) exceed the package.
- Fmax: achieved frequency of `clk` from `r.json`. Report median, min, max over seeds.
- Resources from `r.json` utilisation: LUT4 = TRELLIS_COMB, FF = TRELLIS_FF (minus wrapper
  FFs, known per design), LUTRAM = TRELLIS_RAMW, DP16KD, MULT18X18D. Yosys `stat` kept as
  secondary.
- Designs: corv32 default + 8 C3 configs (RVFI off); **PicoRV32** `picorv32` native
  interface, two configs, both `COMPRESSED_ISA=0, CATCH_MISALIGN=1, CATCH_ILLINSN=1,
  ENABLE_COUNTERS=1, ENABLE_REGS_DUALPORT=1`: (a) *im-small* `ENABLE_MUL=1, ENABLE_DIV=1`,
  rest default; (b) *im-fast* `ENABLE_FAST_MUL=1, ENABLE_DIV=1, BARREL_SHIFTER=1,
  ENABLE_IRQ=1, ENABLE_IRQ_TIMER=1`. **VexRiscv** pregenerated (pythondata-cpu-vexriscv
  642ecfe): (a) `VexRiscv.v` = GenCoreDefault: RV32IM, 5-stage, bypassed, static
  prediction, single-cycle mul, iterative div, 4 KiB I$ + 4 KiB D$ — closest
  microarchitecture; (b) `VexRiscv_Lite.v`: RV32IM, 2 KiB I$, no D$, multi-cycle
  mul/div/shift; (c) `VexRiscv_Min.v`: RV32I, no caches/bypass/prediction — lower-bound
  reference, *not* like-for-like (no M). No cache-less RV32IM variant is pregenerated
  (SPEC §7.7).
- SoC: `syn/ulx3s/corv32_ulx3s.sv` + `ulx3s.lpf` (25 MHz clock, UART, LEDs on GPIO, flash
  via `USRMCLK`), place & route (10 seeds) for SoC Fmax (used by M7) and a bitstream.

## 11. Budgets (targets, not claims)

| Quantity | Target | Ceiling | Basis |
|---|---|---|---|
| corv32 LUT4 (default, core-only) | ≤ 2,500 | 3,500 | rough sum: decode ~150, ALU+shifter ~250, fwd muxes ~150, branch/target ~120, CSR+64-bit counters ~600, div ~200, mul glue ~150, fetch ~150, pipeline muxing ~400 |
| corv32 FF | ≤ 1,400 | | |
| LUTRAM / DSP | regfile only / ≤ 4 MULT18X18D | | |
| Fmax median (LFE5U-25F-6) | ≥ 75 MHz | | goal; expected critical paths: EX fwd → compare → `ibus_addr`; EX fwd → address adder → `dbus_addr`; BRAM → decode → hazard → buffer enables |
| CoreMark CPI | ≈ 1.20 | 1.45 | §7.8 |

## 12. Experiments plan (Step 6)

### 12.1 Configurations
- Baselines: PicoRV32 im-small, im-fast; VexRiscv default, Lite, Min (§10).
- Ablations (C3): full factorial FORWARDING × MUL_FAST × REDIRECT_REG (8 configs). Each
  config must pass RISCOF and a 100k-instruction lockstep subset before it is measured.
- Compiler sensitivity (secondary): `-O2` (primary) vs `-O3 -funroll-loops`.

### 12.2 CoreMark harnesses
`tb/baselines/{picorv32,vexriscv}/`: same SPEC §3 ELF; RAM 1 MiB at 0x8000_0000 with the
fastest legal response (PicoRV32: `mem_ready` same cycle as `mem_valid`; VexRiscv
Wishbone: `ACK` same cycle as `CYC&STB`), CLINT-compatible `mtime` at 0x0200_BFF8 (+1/cycle),
UART LSR at 0x1000_0005 = 0x60 and THR capture, `tohost` monitor; reset vector
0x8000_0000 (`PROGADDR_RESET` / `externalResetVector`). ITERATIONS per core so simulated
time ≥ 10 s at that core's median Fmax (min 10).

### 12.3 Repeats and variance
- Fmax/LUTs: 10 nextpnr seeds (1..10) per design; median, min, max (and IQR) reported;
  any comparison sentence in the paper cites medians and full ranges.
- CoreMark/mcugrad cycles: deterministic; 1 run + 1 reproduction run (must match exactly).
- Lockstep: fixed seed range (§9.4). Mutation: fixed list (§9.6).
- mcugrad (M7): 100 images, mean/min/max cycles per inference, logits bit-exact vs host.
- Every result row carries commit, tool versions, seed (CLAUDE.md).

## 13. Risks

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| 1 | Same-cycle redirect path (EX fwd → compare → `ibus_addr` → BRAM) limits Fmax | low Fmax vs baselines | REDIRECT_REG=1 is already built as an ablation; switch default only via DECISIONS + SPEC change |
| 2 | riscv-formal BMC runtime at depth 24–30 with fetch buffer, CSRs, 64-bit counters | M4 slip | run checks in parallel; try bitwuzla/yices per check (solver choice is not part of the claim); depths can only be *reduced* via SPEC change log |
| 3 | Spike/corv32 legal divergences (WARL CSRs, boot ROM, mtime, interrupt latency) cause false lockstep mismatches | E1.3 noise | pre-declared exclusion list §9.4; interrupt "pend-then-enable" pattern; any new exclusion = SPEC change |
| 4 | Dynamic instruction count higher than assumed → < 2.0 CoreMark/MHz | E1.4 fails | measure N on Spike in Step 3; add static BTFN in D if N×CPI > 450k |
| 5 | Mutation study compute (formal per mutant) | C1 slip | pre-registered compute guard §9.6; Spike logs cached; early stop within a method |
| 6 | Yosys vs Verilator SV-subset differences | lint churn | `make lint` runs both from the walking skeleton on |
| 7 | nextpnr `--out-of-context` quirks on ECP5 | M6 numbers | fallback (DECISIONS entry): in-context with pin-reducing shift-register wrapper, applied identically to all designs |
| 8 | Baseline harness errors make baselines look worse | C2 credibility | baselines must print "Correct operation validated"; harnesses favour baselines (ideal memory) |
| 9 | Stale-operand bug when EX stalls and producers leave WB | silent corruption | §7.2 re-capture rule; directed test + bug-1/2 style formal coverage |
