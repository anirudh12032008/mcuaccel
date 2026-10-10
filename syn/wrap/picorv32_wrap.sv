// Synthesis wrapper (DESIGN 10.5): PicoRV32 in the SPEC 7 configuration (same parameters as
// rtl/soc/soc_top.sv), one register on every input and output, no logic.
module picorv32_wrap (
    input  logic        clk,
    input  logic        rst,
    output logic        trap,
    output logic        mem_valid,
    output logic        mem_instr,
    input  logic        mem_ready,
    output logic [31:0] mem_addr,
    output logic [31:0] mem_wdata,
    output logic [3:0]  mem_wstrb,
    input  logic [31:0] mem_rdata
);
  logic        rst_q, mem_ready_q;
  logic [31:0] mem_rdata_q;
  logic        c_trap, c_mem_valid, c_mem_instr;
  logic [31:0] c_mem_addr, c_mem_wdata;
  logic [3:0]  c_mem_wstrb;

  always_ff @(posedge clk) begin
    rst_q       <= rst;
    mem_ready_q <= mem_ready;
    mem_rdata_q <= mem_rdata;
    trap        <= c_trap;
    mem_valid   <= c_mem_valid;
    mem_instr   <= c_mem_instr;
    mem_addr    <= c_mem_addr;
    mem_wdata   <= c_mem_wdata;
    mem_wstrb   <= c_mem_wstrb;
  end

  /* verilator lint_off PINCONNECTEMPTY */
  picorv32 #(
      .ENABLE_COUNTERS(1), .ENABLE_COUNTERS64(1), .ENABLE_REGS_16_31(1),
      .ENABLE_REGS_DUALPORT(1), .LATCHED_MEM_RDATA(0), .TWO_STAGE_SHIFT(1),
      .BARREL_SHIFTER(1), .TWO_CYCLE_COMPARE(0), .TWO_CYCLE_ALU(0), .COMPRESSED_ISA(0),
      .CATCH_MISALIGN(1), .CATCH_ILLINSN(1), .ENABLE_PCPI(0), .ENABLE_MUL(0),
      .ENABLE_FAST_MUL(1), .ENABLE_DIV(1), .ENABLE_IRQ(0), .ENABLE_TRACE(0),
      .PROGADDR_RESET(32'h8000_0000), .STACKADDR(32'h8000_0000 + 32'd262144)
  ) u_cpu (
      .clk(clk), .resetn(!rst_q), .trap(c_trap),
      .mem_valid(c_mem_valid), .mem_instr(c_mem_instr), .mem_ready(mem_ready_q),
      .mem_addr(c_mem_addr), .mem_wdata(c_mem_wdata), .mem_wstrb(c_mem_wstrb),
      .mem_rdata(mem_rdata_q),
      .mem_la_read(), .mem_la_write(), .mem_la_addr(), .mem_la_wdata(), .mem_la_wstrb(),
      .pcpi_valid(), .pcpi_insn(), .pcpi_rs1(), .pcpi_rs2(),
      .pcpi_wr(1'b0), .pcpi_rd(32'd0), .pcpi_wait(1'b0), .pcpi_ready(1'b0),
      .irq(32'd0), .eoi(), .trace_valid(), .trace_data());
  /* verilator lint_on PINCONNECTEMPTY */
endmodule
