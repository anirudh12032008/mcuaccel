# DESIGN — mcuaccel

Status: v1.1 (Architect 2026-10-10; red-team amendments D15, orchestrator). Normative for implementers: names, widths, encodings and
cycle behaviour below are the contract between modules, the backend, the M1 model and the
tests. Change an interface only through docs/DECISIONS.md plus an edit here. docs/SPEC.md
says *what* and how claims are judged; this file says *how*.

## 0. Conventions

- RTL: CLAUDE.md subset. `logic`, `always_ff @(posedge clk)`, `always_comb`, `localparam`,
  packed vectors; no interfaces, structs in ports, packages, `unique case`, or `string`-typed
  parameters (untyped `parameter INIT_FILE = ""` for file names). One module per file, file =
  module name, prefix `mcuaccel_` (rtl/accel) / `soc_` (rtl/soc). Synchronous active-high
  `rst`, single clock `clk`. Reset only control state (valids, FSMs, counters, credits);
  datapath registers and memories are not reset. `make lint` clean (Verilator `-Wall`, Yosys).
- Byte addresses; bus addresses word aligned (`addr[1:0] == 0`); little endian: the byte at
  address `4k+i` is lane `i` = `data[8i+7:8i]`.
- "Cycle t" is the interval between rising edges; a register "loads at the end of t".
- Packed vectors of words: word `p` of a vector `v` is `v[32p+31:32p]`; byte `l` of word `p`
  is `v[32p+8l+7 : 32p+8l]`.
- `P` = output channels per tile (parameter, 1..16, default 8). `NACC = max(P, 4)`.
  `LANES = 4` (fixed: one 32-bit word of int8).

## 1. Block diagram

```
                         +------------------------------ soc_top ------------------------------+
  +-----------+  native  | +------------------+  corv32 bus   +------------------+             |
  | picorv32  |--------->| | soc_picorv32_    |-------------->| soc_interconnect |--> err resp |
  | (RV32IM,  |<---------| | adapter          |<--------------|  decode/route    |             |
  |  rdcycle) |          | +------------------+               +--+--------+---+--+             |
  +-----------+          |                         0x8000_0000 |  0x3000 |   | 0x1000_0000    |
                         |                              port A |  _0000  |   v                |
                         |   +---------------------------------v--+      |  +-------------+   |
                         |   | soc_ram (true dual port, 1 MiB sim)|      |  | soc_console |-->putc, tohost
                         |   +---------------------------------^--+      |  +-------------+   |
                         |                              port B |         | slave (regs)       |
                         |   +---------------------------------+---------v------------------+ |
                         |   | mcuaccel_top                    master    mcuaccel_regs       | |
                         |   |                                   |            | start/desc  | |
                         |   |  mcuaccel_fetch (bus master, 1 outstanding, tag per request) | |
                         |   |     ^ rd req (ctrl)   ^ wr req (writer / stamp)  | responses  | |
                         |   |     |                 |                          v            | |
                         |   |  mcuaccel_ctrl ----+  |     desc -> ctrl regs, w -> wbuf,    | |
                         |   |  (descriptor FSM,  |  |     params -> pbuf, x -> align       | |
                         |   |   loop nest, addr  |  |                                      | |
                         |   |   gen, credits)    |  |  mcuaccel_align -> mcuaccel_mac_array | |
                         |   |       | tile info  |  |   (4 lanes x P outputs, NACC acc)     | |
                         |   |       v            |  |        | acc vector (end of tile)    | |
                         |   |  mcuaccel_requant <-+--+--------+  (+ mcuaccel_pbuf params)   | |
                         |   |       | bytes            mcuaccel_wbuf (512 x 4P bytes) ---> mac
                         |   |       v                                                      | |
                         |   |  mcuaccel_writer --> wr req (byte-enable word writes)         | |
                         |   +--------------------------------------------------------------+ |
                         +---------------------------------------------------------------------+
```

The CPU writes `DESC_ADDR` and `CTRL.START`; the accelerator reads a chain of descriptors from
RAM, for each one loads packed weights and per-channel parameters into local buffers, then
streams activations from RAM through its MAC array one 32-bit word per cycle and writes int8
outputs back. The CPU polls `STATUS`. No arbiter: CPU and accelerator use different RAM ports.

## 2. Workloads (exact shapes from the generated model.c)

Source: `third_party/mcugrad/firmware/generated/{mnist_cnn,cifar_mobilenet}/model.c`, the
`op_*` calls. N = 1 everywhere. MACs = in-bounds multiply-accumulates (padded taps excluded).

**mnist_cnn** (input zp -95, 7 ops; arena 7840 B)

| op | kernel | H×W×C in | O | K, S, P | out | x_zp | MACs | where |
|---|---|---|---|---|---|---|---|---|
| op_0 | transpose NCHW→NHWC (1,1,28,28) | 1×28×28 | – | – | 28×28×1 | – | – | sw (identity bytes) |
| op_1 | conv2d groups 1 | 28×28×1 | 8 | 3, 1, 1 | 28×28×8 | -95 | 53,792 | accel conv |
| op_2 | maxpool 2×2 s2 | 28×28×8 | – | – | 14×14×8 | – | – | sw |
| op_3 | conv2d groups 1 | 14×14×8 | 16 | 3, 1, 1 | 14×14×16 | -128 | 204,800 | accel conv |
| op_4 | maxpool 2×2 s2 | 14×14×16 | – | – | 7×7×16 | – | – | sw |
| op_5 | transpose NHWC→NCHW | 7×7×16 | – | – | 16×49 | – | – | sw |
| op_7 | fc | K=784 | 10 | – | 10 | -128 | 7,840 | accel conv (fc) |

**cifar_mobilenet** (input zp -7, 15 ops; arena 20480 B). dw = groups == C == O.

| op | kernel | H×W×C in | O | K, S, P | out | x_zp | MACs | where |
|---|---|---|---|---|---|---|---|---|
| op_0 | conv | 32×32×3 | 16 | 3, 1, 1 | 32×32×16 | -7 | 424,128 | conv |
| op_1 | dw | 32×32×16 | 16 | 3, 2, 1 | 16×16×16 | -128 | 35,344 | dw |
| op_2 | conv 1×1 | 16×16×16 | 32 | 1, 1, 0 | 16×16×32 | -128 | 131,072 | conv |
| op_3 | dw | 16×16×32 | 32 | 3, 1, 1 | 16×16×32 | -128 | 67,712 | dw |
| op_4 | conv 1×1 | 16×16×32 | 32 | 1, 1, 0 | 16×16×32 | -128 | 262,144 | conv |
| op_5 | dw | 16×16×32 | 32 | 3, 2, 1 | 8×8×32 | -128 | 16,928 | dw |
| op_6 | conv 1×1 | 8×8×32 | 64 | 1, 1, 0 | 8×8×64 | -128 | 131,072 | conv |
| op_7 | dw | 8×8×64 | 64 | 3, 1, 1 | 8×8×64 | -128 | 30,976 | dw |
| op_8 | conv 1×1 | 8×8×64 | 64 | 1, 1, 0 | 8×8×64 | -128 | 262,144 | conv |
| op_9 | dw | 8×8×64 | 64 | 3, 2, 1 | 4×4×64 | -128 | 7,744 | dw |
| op_10 | conv 1×1 | 4×4×64 | 128 | 1, 1, 0 | 4×4×128 | -128 | 131,072 | conv |
| op_11 | dw | 4×4×128 | 128 | 3, 1, 1 | 4×4×128 | -128 | 12,800 | dw |
| op_12 | conv 1×1 | 4×4×128 | 128 | 1, 1, 0 | 4×4×128 | -128 | 262,144 | conv |
| op_13 | avgpool 4×4 s4 | 4×4×128 | – | – | 1×1×128 | – | – | sw |
| op_15 | fc | K=128 | 10 | – | 10 | -128 | 1,280 | conv (fc) |

All conv/fc in both models: y_zp = -128 for hidden layers (act_min = -128 = y_zp: fused relu),
the final fc has y_zp = -15 (mnist) / -45 (cifar), clamp [-128, 127]. Total accelerated MACs:
266,432 (mnist), 1,776,560 (cifar). Chains (consecutive accelerated ops): cifar {op_0..op_12},
{op_15}; mnist {op_1}, {op_3}, {op_7}.

## 3. Bus protocol (corv32 DESIGN §3, restated; normative)

A simplified OBI-style protocol: address phase `req`/`gnt`, response phase `rvalid`, **at most
one outstanding request per master**. Master → slave: `req`, `addr[31:0]` (word aligned),
`we`, `be[3:0]`, `wdata[31:0]`. Slave → master: `gnt`, `rvalid`, `rdata[31:0]`, `err`.

1. **Address phase.** A request is *accepted* at the end of a cycle in which `req && gnt`.
   `gnt` may depend combinationally on `req`/`addr`/`we` of the same cycle.
2. **Hold.** Once the master raises `req`, it keeps `req` high and `addr/we/be/wdata` stable
   until the request is accepted (no withdrawal).
3. **One outstanding.** The master may raise `req` in cycle t only if no accepted request is
   still awaiting its response, **or** that response is delivered in cycle t (`rvalid` = 1 in
   t). Hence a one-cycle-latency slave sustains one transfer per cycle.
4. **Response.** Exactly one response per accepted request, in order, in a cycle strictly after
   the acceptance cycle: `rvalid` high for exactly one cycle, with `rdata` (reads) and `err`.
   Writes are also acknowledged with `rvalid` (`rdata` don't-care). `rvalid/rdata/err` never
   depend combinationally on the same cycle's `req`.
5. **No back-pressure on responses.** The master always accepts `rvalid`.
6. **Errors.** `rvalid && err`: the access had no effect (no write performed) and read data is
   invalid.
7. **Write visibility.** A write is visible to any request (either port) accepted in or after
   the cycle in which its `rvalid` is asserted.
8. **Reset.** While `rst`, masters drive `req = 0`; slaves drive `rvalid = 0`.

Zero-wait slave: `gnt = 1`, `rvalid <= req`, `rdata` registered → 1 transfer/cycle, latency 1.
Every slave in this project (RAM ports, accelerator regs, console, error responder) is
zero-wait; masters must still work with any compliant slave (tests use stalling slaves).
Port naming: slave ports `s_req s_addr s_we s_be s_wdata s_gnt s_rvalid s_rdata s_err`,
master ports `m_*` with the same suffixes, other prefixes as given per module.

## 4. SoC

### 4.1 Memory map (CPU view)

| Base | Size (decoded) | Device | Notes |
|---|---|---|---|
| 0x1000_0000 | 256 B (`addr[31:8] == 24'h100000`) | `soc_console` | sim console |
| 0x3000_0000 | 4 KiB (`addr[31:12] == 20'h30000`) | `mcuaccel_regs` | §6.1 |
| 0x8000_0000 | RAM_BYTES | `soc_ram` port A | code, data, stack; reset PC |
| other | | error responder | gnt=1, rvalid+err next cycle |

RAM_BYTES = 1 MiB (Verilator; stack top 0x8010_0000), 256 KiB for FPGA synthesis. The
accelerator master reaches only RAM (port B); any other address returns `err`. No boot ROM:
RAM is preloaded (`+ram=<hex>` plusarg, or `INIT_FILE` via `$readmemh`, one 32-bit word per
line, corv32 `sw/tools/elf2hex.py` format), PicoRV32 starts at 0x8000_0000.

### 4.2 Console registers (`soc_console`, offset from 0x1000_0000)

| Off | Name | Access | Function |
|---|---|---|---|
| 0x0 | PUTCHAR | W | byte lane 0 → `putc_valid` pulse, `putc_data` |
| 0x4 | TOHOST | W | full word → `tohost_valid` pulse, `tohost_data` (1 = pass, else fail code) |
| 0x8 | CYCLE_LO | R | free-running 64-bit cycle counter, low word (reset 0) |
| 0xC | CYCLE_HI | R | high word |

Other offsets read 0, writes ignored. Firmware uses `rdcycle` for timing; CYCLE_* exist for
the harness and as a cross-check. `bench_micros()` = cycles / (clock_hz / 10^6), clock_hz a
compile-time constant (50 MHz nominal; cycles are the claimed unit).

### 4.3 SoC modules and ports

```systemverilog
module soc_top #(parameter int RAM_BYTES = 1048576, parameter RAM_INIT = "",
                 parameter int P = 8) (
  input  logic        clk, rst,
  output logic        putc_valid,  output logic [7:0]  putc_data,
  output logic        tohost_valid, output logic [31:0] tohost_data,
  output logic        cpu_trap,     // picorv32 trap
  output logic        cpu_bus_err,  // sticky: a CPU access got err
  output logic        accel_busy);
```
Instantiates `picorv32` (parameters SPEC §7), `soc_picorv32_adapter`, `soc_interconnect`,
`soc_ram` (port A ← interconnect, port B ← `mcuaccel_top` master), `soc_console`,
`mcuaccel_top #(.P(P))` (slave ← interconnect).

```systemverilog
module soc_picorv32_adapter (
  input  logic        clk, rst,
  // PicoRV32 native memory interface
  input  logic        mem_valid, mem_instr,
  output logic        mem_ready,
  input  logic [31:0] mem_addr, mem_wdata,
  input  logic [3:0]  mem_wstrb,
  output logic [31:0] mem_rdata,
  // corv32 bus master
  output logic m_req, output logic [31:0] m_addr, output logic m_we, output logic [3:0] m_be,
  output logic [31:0] m_wdata, input logic m_gnt, input logic m_rvalid,
  input  logic [31:0] m_rdata, input logic m_err,
  output logic        bus_err);   // sticky
```
Behaviour: state `pend` (reset 0). `m_req = mem_valid && !pend` (combinational, so a zero-wait
slave answers in the next cycle: same timing as PicoRV32's reference memory).
`m_addr = {mem_addr[31:2], 2'b00}`, `m_we = |mem_wstrb`, `m_be = m_we ? mem_wstrb : 4'hF`,
`m_wdata = mem_wdata`. `pend <= 1` on `m_req && m_gnt`; `pend <= 0` on `m_rvalid`.
`mem_ready = m_rvalid`, `mem_rdata = m_rdata`. `m_rvalid && m_err` sets `bus_err` (PicoRV32
has no fault input; the harness stops on it). Rule 2 holds because PicoRV32 keeps its outputs
stable while `mem_valid`; rule 3 because `m_req` is low while `pend`. `mem_instr` unused.

```systemverilog
module soc_interconnect #(parameter int RAM_BYTES = 1048576) (
  input  logic clk, rst,
  input  logic c_req, input logic [31:0] c_addr, input logic c_we, input logic [3:0] c_be,
  input  logic [31:0] c_wdata, output logic c_gnt, output logic c_rvalid,
  output logic [31:0] c_rdata, output logic c_err,                 // from CPU adapter
  output logic r_req, output logic [31:0] r_addr, output logic r_we, output logic [3:0] r_be,
  output logic [31:0] r_wdata, input logic r_gnt, input logic r_rvalid,
  input  logic [31:0] r_rdata, input logic r_err,                  // RAM port A
  output logic a_req, ... a_*,                                     // accelerator regs (same set)
  output logic k_req, ... k_*);                                    // console (same set)
```
Combinational decode of `c_addr` (§4.1) selects one slave (or the internal error responder);
`*_req = c_req && sel`, other signals broadcast; `c_gnt` = selected slave's `gnt` (1 for the
error responder). On acceptance latch the selected index; `c_rvalid/c_rdata/c_err` are muxed
from the latched index. Error responder: `rvalid <= accept`, `err = 1`, `rdata = 0`.

```systemverilog
module soc_ram #(parameter int BYTES = 1048576, parameter logic [31:0] BASE = 32'h8000_0000,
                 parameter INIT_FILE = "") (
  input  logic clk, rst,
  input  logic a_req, input logic [31:0] a_addr, input logic a_we, input logic [3:0] a_be,
  input  logic [31:0] a_wdata, output logic a_gnt, output logic a_rvalid,
  output logic [31:0] a_rdata, output logic a_err,
  input  logic b_req, ... b_*);   // identical second port
```
Per port: `gnt = 1`; in range ⇔ `addr - BASE < BYTES`; `rvalid <= req`; `err <= req && !in
range`; `rdata <= mem[(addr-BASE)>>2]` (read-first); write bytes with `be` when
`req && we && in range`. `logic [31:0] mem [0:BYTES/4-1]`. `ifndef SYNTHESIS`: `$readmemh`
from `INIT_FILE` if non-empty, and from plusarg `+ram=<file>`. Same-word writes on both ports
in one cycle: undefined (the software contract forbids it, §6.5). Both ports write `mem` from one
`always_ff` block (Verilator `-Wall` MULTIDRIVEN otherwise).

```systemverilog
module soc_console (
  input  logic clk, rst, /* slave port s_* */,
  output logic putc_valid, output logic [7:0] putc_data,
  output logic tohost_valid, output logic [31:0] tohost_data);
```

### 4.4 Verilator harness (`sim/`)
`sim/sim_main.cpp` + `soc_top`: plusargs `+ram=<hex>`, `+max_cycles=<n>` (default 2·10^10),
`+vcd=<file>`. Prints `putc` bytes to stdout; exits on `tohost_valid` (exit code 0 iff data
== 1), on `cpu_trap`, on `cpu_bus_err`, or at max_cycles (codes 2, 3, 4, 5). Prints
`SIM_CYCLES=<n>` at exit. Built with `-O3 --x-assign fast --x-initial fast`, `RAM_BYTES = 1 MiB`.

## 5. Accelerator dataflow

**Output stationary over one pixel and one tile.** For each output pixel (raster order), for
each tile (P output channels in conv mode, 4 channels in dw mode), the reduction streams
activation words from RAM and weight rows from the local weight buffer; the P accumulators
hold the pixel's outputs; then the tile is requantized and written while the next tile runs.

**Conv mode: row runs.** In NHWC, the in-bounds part of one kernel row of one output pixel,
`x[iy, ix0+kx0 .. ix0+kx1-1, 0 .. C-1]`, is a *contiguous* run of bytes. The run of the full
kernel row has `RUN_BYTES = KW·C` bytes and is consumed as `RQ = ceil(RUN_BYTES/4)` quads of 4
bytes; run byte `b` is `(kx, c) = (b div C, b mod C)`. Each cycle the MAC array takes one quad
of x (4 lanes) and one weight-buffer row of 4P bytes (P words, word p = output channel p's 4
weights for the same run bytes) and performs 4P MACs into P accumulators. This handles any C
with one mechanism: C = 1 (mnist op_1: RUN_BYTES = 3, one quad per kernel row instead of 3 taps)
and C = 3 (cifar op_0: 9 bytes, 3 quads) cost no more than C = 4. Unaligned runs go through an
aligner (one extra "prime" word per row when the run start is not word aligned). Border
pixels: rows outside [0, H) are skipped; columns outside [0, W) are clipped to the quads that
contain in-bounds bytes, `j ∈ [j0, j1)`, and lanes outside the in-bounds byte range `[b0, b1)`
are masked (their `(x - x_zp)` forced to 0, exactly the reference's skipped taps).
fc is conv with H = OH = 1, W = OW = N, KH = KW = 1, C = K, O = M.

**Dw mode.** Groups == C == O, C % 4 == 0, aligned. A tile is 4 channels `c0..c0+3`; for each
in-bounds tap one aligned word of x (the 4 channels of one pixel) meets one weight word
(4 channels' tap weights); lane l feeds only accumulator l, using the multipliers of output
group 0. 4 useful MACs per cycle out of 4P (P = 8: 12.5 % utilisation). Dw is therefore
x-fetch bound by construction; reusing x across taps needs line buffers (alternative "lb",
modelled in M1, not built).

**Bandwidth.** Everything goes through one 32-bit master port with one outstanding request:
at most one word per cycle (zero-wait RAM). Conv mode reaches 4P MACs per x word, so it is
memory bound at 4P MACs/cycle when NCH ≥ P and the run is aligned; descriptors, weights
(once per command), parameters (once per command) and output writes all consume port cycles.
The requantizer handles one output per cycle; a tile with fewer x words than outputs is
requant bound (§7). Weights are re-read from the buffer, not from RAM, for every pixel.

## 6. Software interface

### 6.1 Register map (`mcuaccel_regs`, base 0x3000_0000)

Zero-wait slave: `gnt = 1`, `rvalid <= req`, `err = 0`. Writes take effect only with
`be == 4'hF` (partial writes ignored); reads return 0 for unlisted offsets.

| Off | Name | Acc | Reset | Bits |
|---|---|---|---|---|
| 0x00 | ID | RO | 0x4D43_4131 | "MCA1" |
| 0x04 | CONFIG | RO | – | [7:0] P, [15:8] LANES = 4, [31:16] WBUF_ROWS = 512 |
| 0x08 | CTRL | WO | – | bit0 START: start the chain at DESC_ADDR (ignored while BUSY); bit1 CLEAR: clear DONE, ERR |
| 0x0C | STATUS | RO | 0 | bit0 BUSY, bit1 DONE (sticky, set at chain end, cleared by START/CLEAR), bit2 ERR (sticky, cleared by START/CLEAR) |
| 0x10 | DESC_ADDR | RW | 0 | address of the first descriptor (4-byte aligned) |
| 0x14 | CYCLES | RO | 0 | cycles BUSY was high during the last chain (cleared at START) |
| 0x18 | NCMD | RO | 0 | commands completed in the last chain (cleared at START) |
| 0x1C | ERR_ADDR | RO | 0 | address of the access that returned `err`; held until the next START or CLEAR |

8 registers. START sets BUSY at the end of the acceptance cycle of the CTRL write, so any later
STATUS read sees BUSY = 1 until the chain ends. The CPU must not write CTRL.START while BUSY
(ignored) and must not touch tensors, weights, parameters or descriptors of a running chain.

### 6.2 Descriptor (in RAM, 18 words = 72 bytes, 4-byte aligned)

| Word | Off | Name | Bits |
|---|---|---|---|
| 0 | 0x00 | NEXT | address of the next descriptor; 0 = last |
| 1 | 0x04 | CFG | [0] MODE (0 conv/fc, 1 dw); [7:4] KH; [11:8] KW; [15:12] SH; [19:16] SW; [23:20] PH; [27:24] PW; others 0 |
| 2 | 0x08 | IN_HW | [15:0] H, [31:16] W |
| 3 | 0x0C | OUT_HW | [15:0] OH, [31:16] OW |
| 4 | 0x10 | CH | [15:0] C (conv: input channels, must equal XPIX; dw: channels of this command), [31:16] NCH (output channels of this command; dw: = C) |
| 5 | 0x14 | RUN | [15:0] RUN_BYTES = KW·C (dw: 0), [25:16] RQ = ceil(RUN_BYTES/4) (dw: 0) |
| 6 | 0x18 | X_ORIGIN | x_base − PH·XROW − PW·XPIX (+ c0 for a dw split), mod 2^32 |
| 7 | 0x1C | XPIX | bytes between horizontally adjacent input pixels |
| 8 | 0x20 | XROW | bytes between input rows (W·XPIX) |
| 9 | 0x24 | X_STEP_W | SW·XPIX |
| 10 | 0x28 | X_STEP_H | SH·XROW |
| 11 | 0x2C | Y_BASE | address of output pixel (0,0), first channel of this command |
| 12 | 0x30 | YPIX | bytes between consecutive output pixels (full O of the layer) |
| 13 | 0x34 | W_BASE | packed weights (§6.4), 4-byte aligned |
| 14 | 0x38 | WCFG | [9:0] W_ROWS (1..512) rows to load; [25:16] ROWS_PER_TILE (conv KH·RQ, dw KH·KW) |
| 15 | 0x3C | P_BASE | parameter records (§6.4), 4-byte aligned |
| 16 | 0x40 | QPARAM | [7:0] x_zp, [15:8] y_zp, [23:16] act_min, [31:24] act_max (int8, two's complement) |
| 17 | 0x44 | STAMP | written by the accelerator: cycles of this command (§6.3) |

The accelerator reads words 0..16 and writes word 17. Where this section left room for
interpretation, the de-facto semantics are those of the Python executor
`backend/mcuaccel_backend/exec.py` (its module docstring lists every resolution: XPIX = C in all
modes, YPIX = O, Y_BASE = y + o_s, split descriptors contiguous at desc_addr + 72k, shift sign-
extended from bits [5:0], wrap-per-tile accumulation, prime read only when misaligned); the RTL
must match the executor, and the executor is proven equal to int8ops (backend/tests). It does not validate descriptors: the
backend guarantees the constraints of §6.4; behaviour on a violating descriptor is
undefined but terminates (all loop bounds are counters compared with `<`; a zero count runs
zero iterations).

### 6.3 Command execution

For each descriptor: DESC (17 reads) → WLOAD (W_ROWS rows) → PLOAD (3·NCH words) → COMPUTE
(loop nest §7.1) → FLUSH (all MAC/requant/writer work done, no outstanding request) → STAMP
write → next descriptor if NEXT ≠ 0, else DONE. STAMP value = number of cycles from the cycle
the first DESC read of this descriptor is raised up to the cycle before the STAMP write is
raised. On any `err` response: stop issuing, wait for the outstanding response, set ERR and
ERR_ADDR, drop pipeline state, go IDLE (BUSY = 0, DONE = 0). "Drop pipeline state" means, explicitly:
credits reset to 2, both requant FIFOs emptied, writer assembly and pending write cleared (no
further RAM writes), MAC valids cleared, aligner `prev` irrelevant (reloaded by the next prime).
A correct chain started after an aborted one must be bit-exact (tested, §10.2). After an abort
STATUS reads exactly ERR (BUSY = 0, DONE = 0); NCMD counts only commands whose STAMP write was
acknowledged without error, so a command whose STAMP write itself faults is not counted.

### 6.4 Backend packing and limits

**Weights, conv mode** (layer OHWI `w[O][KH][KW][C]`; fc `[M][K]` as O = M, KH = KW = 1,
C = K). Command covers output channels `o_s .. o_s+NCH−1`; tile `t` = channels
`o_s + tP + p`, `p ∈ [0, P)`, `NT = ceil(NCH/P)`. Row index
`r = (t·KH + ky)·RQ + j`, `j ∈ [0, RQ)`; a row is P words; in RAM row r word p byte l is at
`W_BASE + 4·(r·P + p) + l` and holds `w[o_s+tP+p][ky][b div C][b mod C]` with `b = 4j + l`,
or 0 if `b ≥ KW·C` or `tP + p ≥ NCH`. `W_ROWS = NT·KH·RQ`, `ROWS_PER_TILE = KH·RQ`; loaded
words = `W_ROWS·P`.

**Weights, dw mode.** Tile `g` = channels `c0+4g+l`, `l ∈ [0, 4)` (c0 = first channel of the
command). Row `r = g·KH·KW + ky·KW + kx`, **one word** per row in RAM at `W_BASE + 4r`, byte
l = `w[c0+4g+l][ky][kx][0]`. `W_ROWS = (NCH/4)·KH·KW`, `ROWS_PER_TILE = KH·KW`; loaded words =
`W_ROWS`; the word is stored in word 0 of buffer row r.

**Parameters.** Record k (channel `o_s + k`, k ∈ [0, NCH)) is 3 words at `P_BASE + 12k`:
`bias` (IR int32 bias, unfolded), `mult` (int32), `shift` (int32, −31..31; the hardware keeps
bits [5:0]).

**Worked example: cifar op_0, 3×3, C = 3 → O = 16, P = 8, pad 1.** RUN_BYTES = 9, RQ = 3,
NT = 2, ROWS_PER_TILE = 9, W_ROWS = 18, 144 words = 576 bytes. Row `(t, ky, j)`:

| j | lanes l = 0..3 hold (kx, c) |
|---|---|
| 0 | (0,0) (0,1) (0,2) (1,0) |
| 1 | (1,1) (1,2) (2,0) (2,1) |
| 2 | (2,2) 0 0 0 |

Weight `w[9][2][1][2]` (o = 9 → t = 1, p = 1; ky = 2; b = 1·3 + 2 = 5 → j = 1, l = 1) lives in
row 1·9 + 2·3 + 1 = 16, at byte offset 4·(16·8 + 1) + 1 = **517** from W_BASE. Parameter record of
channel 9: bytes 108..119 from P_BASE (bias, mult, shift). Descriptor: CFG = 0x0111_1330,
IN_HW = OUT_HW = 0x0020_0020, CH = 0x0010_0003, RUN = 0x0003_0009, XPIX = 3, XROW = 96,
X_STEP_W = 3, X_STEP_H = 96, X_ORIGIN = input − 99, YPIX = 16, WCFG = 0x0009_0012,
QPARAM = 0x7F80_80F9 (x_zp −7, y_zp −128, act_min −128, act_max 127). Pixel (0,0): iy0 = ix0 =
−1, ky ∈ [1,3), kx0 = 1, kx1 = 3, b0 = 3, b1 = 9, j ∈ [0,3); row ky = 1 starts at run address
A = X_ORIGIN + XROW = input − 3; with `input` aligned, A mod 4 = 1, so the row costs 1 prime
word + 3 quads; quad 0 has lanes 0..2 masked and lane 3 = x[0][0][0].

**Limits the backend enforces** (else split, or fall back to software):
- conv: XPIX = C; PH < KH, PW < KW; KH, KW, SH, SW ≤ 15; H, W, OH, OW, C ≤ 65535;
  `ROWS_PER_TILE ≤ 512` (else fallback); split output channels into commands of
  `NCH = P·floor(512 / ROWS_PER_TILE)` (also ≤ 512) channels: same x, `Y_BASE += o_s`,
  `P_BASE`, `W_BASE` per slab.
- dw: C % 4 = 0, x_base % 4 = 0, XPIX % 4 = 0; split channels into commands of
  `4·floor(512 / (KH·KW))` channels: `X_ORIGIN += c0`, `Y_BASE += c0`, C = NCH = channels.
- Guard: the aligner and border clipping may read up to 6 bytes outside an x tensor; those bytes
  are masked and never written, so the only requirement is that `x − 8 .. x_end + 8` lies inside
  RAM. Enforced in `sw/common/link.ld` with an 16-byte pad at both RAM edges and an `ASSERT` that
  no section lies within 8 bytes of either edge; the backend cannot see `input`'s address, so the
  rule is a link-time property, not a backend check.
- No overlap between a command's x and y ranges. Accumulator bound SPEC §4 rule 3.
- shift ∈ [−31, 31].

### 6.5 Driver and generated code

`sw/driver/mcuaccel.h/.c`:
```c
#define MCUACCEL_BASE 0x30000000u
typedef struct { uint32_t w[18]; } mcuaccel_desc_t;          /* 72 B, 4-aligned */
void mcuaccel_run(volatile mcuaccel_desc_t *first);          /* blocks until done */
```
`mcuaccel_run`: write DESC_ADDR, write CTRL = 1, poll `STATUS & BUSY`, on ERR write the fail
code 0xACCE1 to TOHOST (and spin). The backend's `model.c` keeps mcugrad's structure: kernels
for fallback ops pasted as mcugrad does, packed weights `static const uint32_t aw_<i>[]`,
parameters `static const int32_t ap_<i>[]`, one `static mcuaccel_desc_t ad_<chain>[n]` per
chain (in .data, aligned 4). A static `bind` step on the first `model_run` call fills every
address field (NEXT, X_ORIGIN, Y_BASE, W_BASE, P_BASE) from C expressions; on every call the
driver patches only the fields that depend on `input`/`output` (X_ORIGIN of the first layer,
Y_BASE of the last), then `mcuaccel_run(ad_<chain>)`. The CPU only polls while a chain runs.
Compiler ordering: the descriptor patches are plain stores, so `mcuaccel_run` has
`__asm__ volatile("" ::: "memory")` before the DESC_ADDR/CTRL writes and after the poll loop exits
(no cached arena value may be reused across a chain). Address fields are computed in `uintptr_t`
(`(uintptr_t)input − 99`), never as out-of-object pointer arithmetic. The driver is written before
tag m1 (it is software) so M1 can read its instruction sequence (§9.5). Fairness of the fallback
code: the accelerated build's fallback kernels must be the same machine code as the baseline's;
M5 compares objdump hashes of every `mg_*` function in both ELFs and, if GCC's per-call-site
specialisation (`.constprop`) makes them differ, both builds compile the kernels in one shared
translation unit with `__attribute__((noinline))` (recorded in DECISIONS).

## 7. Timing (normative; the RTL is built to match, the M1 model implements it)

### 7.1 Sequencer loop nest

Symbols: λ = cycles per bus transaction (1 for the zero-wait RAM); `NT = ceil(NCH/P)` (conv)
or `NCH/4` (dw). Per pixel (computed in the 1-cycle PSETUP state):

```
iy0 = oy·SH − PH,  ky0 = max(0, −iy0),  ky1 = min(KH, H − iy0)
ix0 = ox·SW − PW,  kx0 = max(0, −ix0),  kx1 = min(KW, W − ix0)
conv: b0 = kx0·C, b1 = kx1·C, j0 = b0 >> 2, j1 = (b1 + 3) >> 2
pixA = X_ORIGIN + oy·X_STEP_H + ox·X_STEP_W        (incremental adds, no multiply)
row0 = pixA + ky0·XROW                              (mul4: 4-bit × 32-bit shift-add)
ypix = Y_BASE + (oy·OW + ox)·YPIX                   (incremental)
```

```
for each descriptor:
  DESC   issue 17 reads (words 0..16)                                   17λ + 1 cycles
  WLOAD  issue W_ROWS·(conv ? P : 1) reads -> wbuf                       λ·words + 1
  PLOAD  issue 3·NCH reads -> pbuf                                       3λ·NCH + 1
  for oy in [0, OH): for ox in [0, OW):
    PSETUP                                                              1
    for t in [0, NT):                                                   T_tile(t) each
      rowA = row0; wr = t·ROWS_PER_TILE + ky0·(conv ? RQ : KW)
      for ky in [ky0, ky1):
        conv:  s = rowA + 4·j0;  a = s mod 4;  addr = s − a
               if a ≠ 0: issue read addr {X, mac_en=0, a}; addr += 4             (prime)
               for j in [j0, j1):
                 mask[l] = (b0 ≤ 4j + l < b1)
                 issue read addr {X, mac_en=1, a, mask, first, last, wrow = wr + j}
                 addr += 4
               wr += RQ
        dw:    for kx in [kx0, kx1):
                 issue read rowA + kx·XPIX + 4t {X, mac_en=1, a=0, mask=1111, first, last,
                                                 wrow = wr + kx}      (kx0·XPIX via mul4, then +XPIX)
               wr += KW
        rowA += XROW
      first = first issued MAC quad of the tile; last = last one.
      Issuing a request with last = 1 requires credit > 0; it decrements credit and pushes
      tile info {ch0 = conv ? tP : 4t, NV, yaddr = ypix + ch0} to the requantizer.
  FLUSH  wait until MAC, requant, writer idle and no request outstanding   2λ + 10 + NV_last
  STAMP  write STAMP                                                     λ + 1
```

`NV = min(P, NCH − tP)` (conv) or 4 (dw). Writes of tile t (`W_t` word writes,
`W_t = ceil(((yaddr mod 4) + NV)/4)`) are issued by the writer while later tiles are being
fetched; writes have priority over reads, each steals one port slot.

Per-row word count (conv): `R_row = (j1 − j0) + [ (rowA_ky + 4·j0) mod 4 ≠ 0 ]`,
`R(pix) = Σ_{ky ∈ [ky0,ky1)} R_row`; dw: `R(pix) = (ky1 − ky0)·(kx1 − kx0)`.

### 7.2 Tile time and credits

Credits = 2 (two result slots: the requantizer's acc FIFO has depth 2). A credit is taken at
the issue of a tile's last request and returned when the requantizer has fed the tile's last
value into its pipeline. Issue → acc available = λ + 3 cycles (response λ, MAC 2 stages, copy 1).
Steady-state tile time:

```
T_tile(t) = max( λ·(R(pix) + W_t),  NV_t,  ceil((NV_t + λ + 3) / 2) )
T_pix     = 1 + Σ_t T_tile(t)
T_compute = Σ_pix T_pix
T_cmd     = (17λ + 1) + (λ·Wwords + 1) + (3λ·NCH + 1) + T_compute + (2λ + 10 + NV_last)   [= STAMP]
T_chain   = Σ_cmd (T_cmd + λ + 1) + 2
```

(Wwords = W_ROWS·P conv, W_ROWS dw.) The first term is the port bound, the second the
requantizer (one output/cycle), the third the two-credit latency bound for tiles with few
outputs. Example: 1×1, C = 16, P = 8, aligned (cifar op_2): R = 4, W = 2, NV = 8 →
T_tile = max(6, 8, 6) = 8 (requant bound); op_12 (C = 128): max(34, 8, 6) = 34 (port bound).

### 7.4 Known model biases (written before m1)

- The +1 PSETUP cycle per pixel is hidden behind the requantizer when a layer is requant bound
  (T_tile = NV): ≈ +3 % on cifar op_2/4/6. The model keeps the +1 (conservative).
- The last tile's W_t and NV are counted in both T_tile and FLUSH (≤ P + 3 cycles per command).
- Driver constants (§9.5) are estimated from the compiled driver's instructions with PicoRV32's
  CPI table; measured on the stub accelerator before m1 where possible.
- STAMP is self-reported by the ctrl counter; M5/M6 assert per chain that
  `CYCLES == Σ(STAMP + λ + 1) + 2` and count at least three commands independently from a VCD
  (first DESC request raised → STAMP request raised).

### 7.3 Datapath pipeline (relative to the response cycle r of a request with mac_en)

| cycle | stage |
|---|---|
| a (accept) | wbuf read issued with `wrow` (tag field) |
| r = a + λ | response: aligner forms the quad combinationally; wbuf row on its output; MAC S1 registers 4P products at end of r |
| r+1 | MAC S2: sum + accumulate (or load when `first`) at end of r+1 |
| r+2 | if `last`: `out_valid`, acc vector copied into the requant acc FIFO at end of r+2 |
| … | requant S0 (pbuf read, select) → S1 → S2 → S3 → S4 → S5 → byte to writer: 6 cycles per value, 1 value/cycle |
| | writer: a word write is raised in the cycle after its last byte arrives |

The wbuf output holds between reads (read enable only on issue), so it is valid in the
response cycle for any λ ≥ 1 (the next read can be issued at earliest in the response cycle
and appears one cycle later).

## 8. Accelerator RTL modules

Files in `rtl/accel/`. Bus port lists abbreviated `/* slave s_* */`, `/* master m_* */` =
the nine signals of §3.

### 8.1 `mcuaccel_top`
```systemverilog
module mcuaccel_top #(parameter int P = 8) (
  input logic clk, rst,
  /* slave s_* (regs) */, /* master m_* (RAM port B) */,
  output logic busy);
```
Instantiates everything below and the glue: fetch response routing by tag (`DESC` → ctrl,
`WBUF` → wbuf write, `PBUF` → pbuf write, `X` → align/mac); wbuf read on
`rd_accept && tag.dest == X && tag.mac_en`; write-port mux `writer | ctrl stamp` (exclusive by
construction: ctrl stamps only when the writer is idle).

### 8.2 Request tag (20 bits, carried by fetch from accept to response)

| dest [1:0] | fields |
|---|---|
| 0 DESC | [6:2] word index 0..16 |
| 1 WBUF | [10:2] row (0..511), [14:11] word (0..P−1) |
| 2 PBUF | [10:2] channel (0..511), [12:11] sel (0 bias, 1 mult, 2 shift) |
| 3 X | [2] mac_en, [6:3] lane mask, [8:7] a (align), [9] first, [10] last, [19:11] wrow |

### 8.3 `mcuaccel_regs`
```systemverilog
module mcuaccel_regs #(parameter int P = 8) (
  input  logic clk, rst, /* slave s_* */,
  output logic start,              // 1-cycle pulse on CTRL.START when !busy
  output logic [31:0] desc_addr,
  input  logic busy, cmd_done, chain_done, err,   // pulses except busy
  input  logic [31:0] err_addr);
```
Owns STATUS bits, CYCLES (counts while `busy`, cleared on start), NCMD (+1 per `cmd_done`).

### 8.4 `mcuaccel_fetch` (the only bus master)
```systemverilog
module mcuaccel_fetch (
  input  logic clk, rst,
  input  logic rd_valid, input logic [31:0] rd_addr, input logic [19:0] rd_tag,
  output logic rd_accept,
  input  logic wr_valid, input logic [31:0] wr_addr, input logic [3:0] wr_be,
  input  logic [31:0] wr_data, output logic wr_accept,
  output logic resp_valid, resp_we, resp_err,
  output logic [31:0] resp_rdata, resp_addr, output logic [19:0] resp_tag,
  output logic idle,                       // nothing outstanding and no request held
  /* master m_* */);
```
`can = !outstanding || m_rvalid` (rule 3). When `can`, raise `m_req` for the write if
`wr_valid`, else the read if `rd_valid`; once raised and not granted, the selection is held
(`hold` register) until `m_gnt` (rule 2) even if a write appears. Accept = `m_req && m_gnt`;
capture `{tag, we, addr}`; `resp_* = m_rvalid, m_rdata, m_err` + captured fields. Requesters
keep `*_valid` and their payload stable until `*_accept`.

### 8.5 `mcuaccel_ctrl` (sequencer)
```systemverilog
module mcuaccel_ctrl #(parameter int P = 8) (
  input  logic clk, rst,
  input  logic start, input logic [31:0] desc_addr,
  output logic busy, cmd_done, chain_done, err, output logic [31:0] err_addr,
  output logic rd_valid, output logic [31:0] rd_addr, output logic [19:0] rd_tag,
  input  logic rd_accept,
  output logic st_valid, output logic [31:0] st_addr, st_data, input logic st_accept,
  input  logic resp_valid, resp_we, resp_err, input logic [31:0] resp_rdata, resp_addr,
  input  logic [19:0] resp_tag,
  input  logic fetch_idle, mac_idle, rq_idle, wr_idle,
  output logic dw, output logic [7:0] x_zp, y_zp, act_min, act_max,
  output logic tile_push, output logic [8:0] tile_ch0, output logic [4:0] tile_nv,
  output logic [31:0] tile_yaddr,
  input  logic credit_ret);
```
States: IDLE, DESC, DESC_WAIT, WLOAD, WLOAD_WAIT, PLOAD, PLOAD_WAIT, PSETUP, ISSUE, FLUSH,
STAMP, STAMP_WAIT, ABORT. `*_WAIT` = until `fetch_idle` (all responses in; one bubble, in the
model's `+1`). Descriptor words are captured from DESC responses (`tag.dest == DESC`). Loop
counters per §7.1, all incremental; four `mul4` shift-add products in PSETUP: `ky0·XROW`,
`kx0·(dw ? XPIX : C)`, `kx1·C`, `ky0·(dw ? KW : RQ)` (k ≤ 15, written as 4 conditional adds,
no `*`, so no DSP). Credit counter 0..2 (reset 2). STAMP cycle counter per command.

### 8.6 `mcuaccel_wbuf`
```systemverilog
module mcuaccel_wbuf #(parameter int P = 8) (
  input  logic clk,
  input  logic we, input logic [8:0] waddr, input logic [3:0] wword, input logic [31:0] wdata,
  input  logic re, input logic [8:0] raddr, output logic [32*P-1:0] rdata);
```
P memories `logic [31:0] m_p [0:511]` (generate), memory p written when `wword == p`;
synchronous read of all P at `raddr` when `re`, output held otherwise. Maps to P DP16KD
(512×36). 512 rows × 4P bytes = 16 KiB at P = 8.

### 8.7 `mcuaccel_pbuf`
```systemverilog
module mcuaccel_pbuf (
  input  logic clk,
  input  logic we, input logic [8:0] waddr, input logic [1:0] wsel, input logic [31:0] wdata,
  input  logic re, input logic [8:0] raddr,
  output logic [31:0] bias, mult, output logic [5:0] shift);
```
Three 512-deep memories (bias 32 b, mult 32 b, shift 6 b), synchronous read, held when !re.

### 8.8 `mcuaccel_align`
```systemverilog
module mcuaccel_align (
  input  logic clk,
  input  logic in_valid, input logic [31:0] in_word, input logic [1:0] in_a,
  output logic [31:0] quad);
```
`prev <= in_word` on every `in_valid`. `quad = in_a == 0 ? in_word :` low 32 bits of `{in_word, prev} >> (8·in_a)`
(combinational). With run start A, `a = A mod 4`: word `k` holds run bytes from `4k − a`; on
arrival of word `j+1`, `{cur, prev} >> 8a` gives run bytes `4j .. 4j+3`. The prime word only
loads `prev`. Dw always has `a = 0`.

### 8.9 `mcuaccel_mac_array`
```systemverilog
module mcuaccel_mac_array #(parameter int P = 8) (
  input  logic clk, rst,
  input  logic in_valid, in_first, in_last, input logic [31:0] in_quad,
  input  logic [3:0] in_mask, input logic [7:0] x_zp, input logic dw,
  input  logic [32*P-1:0] in_w,
  output logic out_valid, output logic [32*((P > 4) ? P : 4)-1:0] out_acc,
  output logic idle);
```
S1 (end of r): `xs[l] = in_mask[l] ? $signed(x_l) − $signed(x_zp) : 9'sd0` (9 b signed; all arithmetic signed);
`prod[p][l] = xs[l] · $signed(w[p][l])` (17 b) registered, with valid/first/last.
S2 (end of r+1): conv: `sum[p] = Σ_l prod[p][l]` (19 b), `acc[p] <= first ? sext(sum[p]) :
acc[p] + sext(sum[p])` for p < P; dw: `acc[l] <= first ? sext(prod[0][l]) : acc[l] +
sext(prod[0][l])` for l < 4. Accumulators wrap mod 2^32 (exact, SPEC §4). S3: `out_valid` =
registered (S2 valid && last); `out_acc` = the acc registers (stable during that cycle).
`idle` = no valid in S1/S2/S3. 4P multipliers, NACC accumulators.

### 8.10 `mcuaccel_requant`
```systemverilog
module mcuaccel_requant #(parameter int P = 8) (
  input  logic clk, rst,
  input  logic acc_valid, input logic [32*((P > 4) ? P : 4)-1:0] acc_in,
  input  logic tile_push, input logic [8:0] tile_ch0, input logic [4:0] tile_nv,
  input  logic [31:0] tile_yaddr,
  input  logic [7:0] y_zp, act_min, act_max,
  output logic pb_re, output logic [8:0] pb_raddr,
  input  logic [31:0] pb_bias, pb_mult, input logic [5:0] pb_shift,
  output logic out_valid, out_last, output logic [31:0] out_addr, output logic [7:0] out_byte,
  input  logic out_ready,
  output logic credit_ret, idle);
```
Two FIFOs of depth 2: tile info (pushed at issue) and acc vectors (pushed by `acc_valid`),
popped together. Drain index i = 0..NV−1 of the head entry, one per cycle when not stalled.
Stall = `!out_ready`: freezes every stage (pbuf `re` low, so its output holds).

| stage | operation (all integer, widths exact) |
|---|---|
| S0 | `pb_raddr = ch0 + i`, `pb_re = 1`; register `acc = head[i]`, `addr = yaddr + i`, `last = (i == NV−1)`; after the last value pop both FIFOs, pulse `credit_ret` |
| S1 | `s = acc + bias` (32 b wrap); `ls = shift > 0 ? shift : 0`, `rs = shift > 0 ? 0 : −shift`; `av = s << ls` (32 b wrap) |
| S2 | `prod = $signed(av) · $signed(mult)` (64 b) |
| S3 | `x = (av == INT32_MIN && mult == INT32_MIN) ? INT32_MAX : (prod + 2^30)[62:31]` |
| S4 | `mask = (1 << rs) − 1`; `r = (x >>> rs) + (((x & mask) > ((mask >> 1) + (x < 0))) ? 1 : 0)` |
| S5 | `v = sext33(r) + sext33(y_zp)`; `y = v < sext33(act_min) ? act_min : (v > sext33(act_max) ? act_max : v)` — **signed** compares (the 8-bit ports are two's complement; cast with `$signed`); out byte = y[7:0] |

S3's closed form equals SRDHM's truncating-nudge form (proof in mg_int8.h lines 62-67). The
special case compares S1's `av` and the mult (both carried to S3). One 32×32 multiplier (4
MULT18X18D). Latency 6, throughput 1 value/cycle.

### 8.11 `mcuaccel_writer`
```systemverilog
module mcuaccel_writer (
  input  logic clk, rst,
  input  logic in_valid, in_last, input logic [31:0] in_addr, input logic [7:0] in_byte,
  output logic in_ready,
  output logic wr_valid, output logic [31:0] wr_addr, output logic [3:0] wr_be,
  output logic [31:0] wr_data, input logic wr_accept,
  output logic idle);
```
Assembles bytes (consecutive addresses within a tile) into a word: lane `in_addr[1:0]`,
`be` bit set. When the byte has `in_addr[1:0] == 3` or `in_last`, the word moves to the
1-entry pending write (`wr_*`), assembly restarts empty. `in_ready = !wr_valid || wr_accept`.

### 8.12 Area / resource estimate (P = 8; ECP5 TRELLIS_COMB)

| block | LUT4 | FF | DSP | DP16KD |
|---|---|---|---|---|
| regs (8 regs, read mux, counters) | 250 | 170 | | |
| fetch | 150 | 70 | | |
| ctrl (FSM, counters, 4 mul4, address adders, compares) | 1300 | 900 (incl. 17×32 descriptor) | | |
| align | 70 | 32 | | |
| mac array (32 mults in DSP, 8 adder trees, 8 acc, dw mux) | 1050 | 850 | 32 | |
| requant (2×256 FIFO, 8:1 mux, 2 barrel shifters, round, clamp) | 850 | 850 | 4 | |
| writer | 100 | 75 | | |
| wbuf / pbuf | 50 | | | 8 + 3 |
| **total** | **≈ 3800** | **≈ 2950** | **36** | **11** |

Budgets (SPEC C3): LUT4 ≤ 6000, MULT18X18D ≤ 40, DP16KD ≤ 12; FF ≤ 4000 and Fmax ≥ 50 MHz
are targets. Scaling: DSP = 4P + 4, DP16KD = P + 3, LUT4 ≈ 2100 + 210·P.

## 9. M1 architecture model (`model/`)

### 9.1 What it computes

`model/m1.py` (numpy allowed, no RTL): for each accelerated layer of both networks, three
architectures and `P ∈ {1, 2, 4, 8, 16}`, the predicted cycles, bus words and MAC utilisation;
for the chosen architecture ("os", P = 8) the predicted per-command STAMP and whole
`model_run` time and speedup. Layer list: a literal table equal to §2, cross-checked by a test
that parses the `op_*` calls of the two `model.c` files. Tests: `tb/unit/test_m1_model.py`.

Parameters (defaults): `P`, `lanes = 4`, `lam = 1`, `credits = 2`, `L_mac = 2`, `L_rq = 6`,
`x_align` (x_base mod 4 of the network input, read from the firmware link map — the regenerated
test-set arrays and `s_out` are `__attribute__((aligned(4)))`, so 0 is expected and asserted at bind time), `wbuf_rows = 512`, `pbuf = 512`,
`t_drv_chain`, `t_drv_patch` (driver constants, below), `sw_cycles` (dict: measured baseline
cycles per software op, from the profile build of the baseline on the Step 3 SoC).

### 9.2 Architecture "os" (built)
Exactly §7: iterate pixels and tiles, evaluate `R(pix)`, `W_t`, `T_tile`, `T_pix`, `T_cmd`;
split commands per §6.4 limits (each split command pays DESC/WLOAD/PLOAD/FLUSH/STAMP).
Outputs per command: `t_desc, t_wload, t_pload, t_compute, t_flush, t_cmd (= STAMP)`,
`bus_rd = 17 + Wwords + 3·NCH + Σ R`, `bus_wr = Σ W_t + 1`, `macs` (in-bounds),
`util = macs / (4P · t_compute)`.

Symbolic application to the two networks (aligned, λ = 1, NV = P when NCH ≥ P):
- 1×1 conv, C % 4 = 0, O % P = 0, YPIX % 4 = 0 (cifar op_2,4,6,8,10,12): no borders, `R = C/4`,
  `W = P/4` (P ≥ 4; else 1), `T_compute = OH·OW·(1 + (O/P)·max(C/4 + ceil(P/4), P, ceil((P+4)/2)))`.
- 3×3 dw, pad 1 (cifar op_1,3,5,7,9,11): `R(pix) = rows(oy)·cols(ox)`, `W = 1`, `NV = 4`,
  `T_compute = OH·OW + (C/4)·Σ_pix max(R(pix) + 1, 4)`, with `Σ R = (Σ_oy rows)(Σ_ox cols)`,
  rows(oy) = min(3, H − iy0) − max(0, −iy0).
- 3×3 conv, C ∈ {1, 3, 8} (mnist op_1, op_3, cifar op_0): `R(pix) = Σ_ky ((j1 − j0) + [a_ky ≠ 0])`
  with `a_ky = (X_ORIGIN + oy·X_STEP_H + ox·X_STEP_W + ky·XROW + 4·j0) mod 4`; C = 8 is always
  aligned; C = 1, 3 pay the prime word on 3 of 4 alignments.
- fc (mnist op_7 K = 784; cifar op_15 K = 128), one pixel, `NT = ceil(10/P)`:
  `T_compute = 1 + Σ_t max(K/4 + W_t, NV_t, ceil((NV_t+4)/2))`; WLOAD = `NT·(K/4)·P` dominates.

### 9.3 Architecture "lb" (line buffer, x reuse; modelled, not built)
Same 4P multipliers and port. Conv/fc: per output row oy and tile t, each in-bounds input row is
streamed once into a KH-row line buffer: words = `Σ_{ky in-bounds} (ceil(W·C/4) + 1)`; MAC
cycles = `Σ_ox Σ_ky (j1 − j0)`; `T(oy,t) = max(λ·(words + Σ_ox W_t), MAC cycles, OW·NV_t) + 1`.
Dw: the 4P multipliers are 4 channels × Pb adjacent output pixels of one row,
`Pb = min(P, OW − ox)`; each fetched word (4 channels of one input pixel) is multiplied into
every pixel of the block whose window contains it, so MAC cycles = words. Per (oy, group,
block): `T_blk = 1 + max(λ·(Σ_{ky in-bounds} ncols + Pb), 4·Pb)` with
`ncols = min(W, ixl + KW) − max(0, ixf)`, `ixf = ox·SW − PW`, `ixl = (ox + Pb − 1)·SW − PW`;
`Pb` word writes; `4·Pb` outputs through the single requantizer. Extra cost reported:
line-buffer bytes `KH·W·C` (conv) and `P·4` (dw) or `P·OW` (conv) accumulators.

### 9.4 Architecture "acc" (accumulator SRAM, x fetched once per pixel; modelled)
Conv/fc: `T_pix = 1 + max(λ·(R(pix) + Σ_t W_t), Σ_t Σ_ky (j1 − j0), Σ_t NV_t)` — MAC cycles
unchanged, x read once, writes overlap compute. Dw: identical to "os". Bus words reported.

### 9.5 Driver and software terms
`t_drv_chain` = cycles of `mcuaccel_run` outside the accelerator (2 stores, poll loop exit,
call/return) and `t_drv_patch` per patched descriptor field: estimated in M1 from the
compiled driver's instruction sequence with PicoRV32's CPI table (load 5, store 5, ALU 3,
branch 3/5, jal 3, jalr 6) and recorded with the predictions. Predicted accelerated
`model_run` = Σ software ops (`sw_cycles`, identical code to the baseline) + Σ_chains
(`T_chain + t_drv_chain + patches·t_drv_patch`). Predicted speedup = measured baseline
`model_run` / that. Scoring (SPEC C4b) uses accelerator + driver cycles only; the software term
is a measurement carried through for the speedup figure.

### 9.6 Outputs and protocol
- `results/m1_predictions.csv`: `model, op, kind, arch, P, lam, H, W, C, O, KH, KW, SH, SW, PH,
  PW, macs, t_desc, t_wload, t_pload, t_compute, t_flush, t_cmd, bus_rd, bus_wr, util` (one row
  per command; "os" rows for all P, "lb"/"acc" rows for all P).
- `results/m1_summary.json`: per model × arch × P: accelerator cycles, software cycles, driver
  cycles, predicted model_run, baseline (measured), predicted speedup; seedless, plus commit.
- `results/m1_*.png` (optional): cycles vs P per layer.
Protocol: SPEC C4. M6 `experiments/compare_m1.py` joins `git show m1:results/m1_predictions.csv`
(arch "os") with measured STAMPs (`results/m6_layers.csv`) and writes
`results/m6_pred_vs_meas.csv` with the relative error per command and per network.

## 10. Verification plan

### 10.1 Unit tests (cocotb + Verilator, `tb/unit/`)
- Bus checker `tb/common/bus_checker.py`: asserts rules 1-8 on any port every cycle (hold,
  one outstanding, response count/order, no rvalid without request). Attached to every master
  and slave port in every test. Slave model `tb/common/ram_slave.py`: zero-wait, or random
  `gnt` (p = 0.7) and response latency 1..4 (seeded).
- `mcuaccel_requant`: directed (acc ∈ {INT32_MIN, −1, 0, 1, INT32_MAX}, mult ∈ {INT32_MIN,
  −1, 0, 2^30, INT32_MAX}, shift ∈ {−31, −1, 0, 1, 31}, the MIN·MIN case, clamp bounds equal,
  y_zp extremes making r + y_zp exceed int32) + 10^5 random vs `int8ops.requant` + clamp; stalls.
- `mcuaccel_mac_array`: random quads/masks/weights/first/last vs a Python model, both modes, P ∈ {1, 4, 8, 16}.
- `mcuaccel_align`: all 4 alignments, random words.
- `mcuaccel_writer`: random start alignment and NV, random `wr_accept` delays; expected byte writes.
- `mcuaccel_fetch`: random read/write mixes against stalling slaves; tag/addr returned match.
- `mcuaccel_regs`, `soc_console`, `soc_ram` (both ports, be, err), `soc_interconnect`
  (decode, error responder), `soc_picorv32_adapter` (with a PicoRV32-like driver model).
- `mcuaccel_ctrl`: address/tag stream for small layers vs a Python golden generator of §7.1.

### 10.2 Accelerator level (`tb/accel/`)
Random layer generator (seed s, `numpy.random.default_rng(s)`): mode conv 50 % / dw 30 % / fc
20 %; KH, KW ∈ 1..5, SH, SW ∈ 1..3, PH ∈ [0, KH), PW ∈ [0, KW); H, W ∈ 1..12 with OH, OW ≥ 1;
conv C ∈ 1..20, O ∈ 1..40 (5 % of seeds O ∈ 513..600 with KH = KW = C = 1 to force splits);
dw C ∈ {4, 8, …, 32}; fc K ∈ 1..300, M ∈ 1..40, N ∈ 1..3. x, w uniform int8 (full range,
including −128); x_zp, y_zp uniform int8; bias uniform ±2^20 (5 % at the no-wrap bound);
mult uniform [2^30, 2^31) (85 %) or any int32; shift uniform [−31, 31] (30 %) or [−12, 2];
act_min ≤ act_max random (50 % full range); x and y base alignment 0..3 (conv/fc), slave
stalls in 30 % of seeds. Flow: backend packs and builds descriptors → RAM model → RTL →
compare with `int8ops`. Seeds 0..999 (C2a, `results/m3_bitexact.csv`: seed, mode, shape,
cycles, mismatches). Plus: descriptor chains of 2-4 random commands, an err response mid
command (ERR set, ERR_ADDR right, no hang), START while busy ignored.
Whole networks (C2b): for 10 images, run the int8 graph node by node; accelerated nodes on
the RTL (via backend descriptors), others through `int8ops`; compare every tensor with
`sim.run_int8`. The graphs come from mcugrad's own pipeline (`bench/bench_codegen.py build`)
on its pinned weights; a test checks that `mcugrad.codegen.emit` of that graph reproduces the
committed `model.c` byte for byte (same model).

### 10.3 Backend (`backend/tests/`)
Packing round-trip, descriptor fields, splits, bound checks, fallback choice; a pure-Python
executor of descriptor semantics (§6.2, §7.1, reusing `int8ops.requant`) equals
`int8ops.conv2d_nhwc_s8` on 1000 random layers (fast, no RTL) — the reference the RTL tests
also use to localise failures.

### 10.4 SoC and full system (`tb/soc/`, `sim/`)
Test sets: mcugrad's generated `testset.c` (1000 / 400 images) does not fit 1 MiB RAM and would
take ~10^11 cycles; `sw/gen_testset.py` regenerates them with `emit_testset(..., n)` from the same
quantized images (n = 10 for timing, n = 100 for C2c), arrays `__attribute__((aligned(4)))`,
and `-DBENCH_RUNS=10` (timing) / `-DBENCH_RUNS=1 -DBENCH_DUMP_ARGMAX` (C2c).
cocotb: firmware smoke tests (putchar, tohost, rdcycle, accelerator register access, one
small command). Verilator C++: baseline and accelerated builds of both networks; C2c (100
images, output bytes printed with `BENCH_DUMP_ARGMAX` and compared with `sim.run_int8`); C1/C5
(10 images × 10 runs); profile build for per-layer numbers; ablation builds P ∈ {1, 2, 4, 16}.

### 10.5 Synthesis (`syn/`)
`syn/wrap/mcuaccel_top_wrap.sv` (registers on all ports), Yosys `synth_ecp5`, nextpnr-ecp5
`--85k --package CABGA381 --speed 6 --out-of-context --freq 50`, `HOME=.cache/home`, seeds
1..10 → `results/syn/*.csv`; same flow for PicoRV32 (SPEC §7 config) and for `soc_top`
(RAM 256 KiB) with and without the accelerator. Lint in CI at every commit.

## 11. Budgets (targets; claims in SPEC C3)

| Quantity | Target | Ceiling | Basis |
|---|---|---|---|
| mcuaccel_top P=8 LUT4 | ≈ 3800 | 6000 (C3) | §8.12 |
| MULT18X18D | 36 | 40 (C3) | 4P + 4 |
| DP16KD | 11 | 12 (C3) | P + 3 |
| FF | ≈ 2950 | 4000 | §8.12 |
| Fmax (OOC, median of 10 seeds) | ≥ 50 MHz | – | critical path expected in requant S2/S3 (32×32 DSP + 64-bit add) or ctrl PSETUP mul4 → address adder |
| cifar accelerator cycles, P = 8 | ≈ 1.4·10^5 | – | §7 applied by hand; M1 is the prediction |

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Requant bound on small-C 1×1 layers (NV > R + W) | modelled (T_tile); ablation shows it; a second requant lane is the upgrade, not planned |
| Yosys maps the 9×8 products to one MULT18X18D each → DSP grows 4P | budgeted (36 ≤ 40); P = 16 needs 68, reported as is |
| Fmax limited by the 32×32 multiply or PSETUP | add a register stage (S2a/S2b) or split PSETUP into 2 cycles **before tag m1**, else report the change as post-hoc |
| Aligner/border reads outside a tensor | guard rule §6.4; RAM err → ERR, never silent |
| Software fallback dominates mnist (Amdahl) | C5 threshold accounts for it; optional passes SPEC §3 |
| Verilator time for 10×10 baseline cifar (≈ 3-7·10^9 cycles, ≈ 10-60 min at 2-5 MHz) | deterministic; run it once per build; no parallel-image fallback (it would break bench_main's protocol) |
| PicoRV32 bus adapter adds latency vs native memory | same SoC for both builds, so the ratio is fair; adapter timing documented §4.3 |
| Model and RTL timing diverge | the model implements §7 literally; the ctrl unit test checks the request stream; mismatch above 20 % is reported, not tuned away |

## 13. Milestones → deliverables

| M | Deliverables | Gate |
|---|---|---|
| M1 | `model/m1.py`, `tb/unit/test_m1_model.py`, `results/m1_predictions.csv`, `results/m1_summary.json` (baseline measured on the Step 3 SoC) | tag m1 before any datapath RTL (SPEC C4) |
| M2 | `rtl/accel/mcuaccel_*.sv` (§8), lint clean, unit tests passing | requant + mac unit tests vs int8ops |
| M3 | `tb/accel/` random layers (C2a), whole networks (C2b), chains, errors | 0 mismatches, `results/m3_bitexact.csv` |
| M4 | `backend/mcuaccel_backend/` (graph walk, packing, splitting, descriptors, model.c emit, driver), `sw/driver/`, backend tests | executor == int8ops on 1000 layers; emitted C builds with `-Werror` |
| M5 | `rtl/soc/soc_*.sv`, `sim/`, firmware platform, both builds of both networks in Verilator | C2c, first C1/C5 measurements |
| M6 | `syn/`, `results/syn/*.csv`, `experiments/` (speedup, per-layer, P ablation, pred vs meas) | C1, C3, C4, C5 judged and written to results/ |
