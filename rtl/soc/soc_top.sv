// SoC top (DESIGN 4): PicoRV32 + adapter + interconnect + dual-port RAM + console + accelerator.
module soc_top #(
    parameter int RAM_BYTES = 1048576,
    parameter RAM_INIT = "",
    parameter int P = 8
) (
    input  logic        clk,
    input  logic        rst,
    output logic        putc_valid,
    output logic [7:0]  putc_data,
    output logic        tohost_valid,
    output logic [31:0] tohost_data,
    output logic        cpu_trap,
    output logic        cpu_bus_err,
    output logic        accel_busy
);
  // PicoRV32 native interface
  logic        mem_valid, mem_instr, mem_ready;
  logic [31:0] mem_addr, mem_wdata, mem_rdata;
  logic [ 3:0] mem_wstrb;

  // CPU-side bus
  logic        c_req, c_we, c_gnt, c_rvalid, c_err;
  logic [31:0] c_addr, c_wdata, c_rdata;
  logic [ 3:0] c_be;
  // RAM port A, accelerator regs, console
  logic        r_req, r_we, r_gnt, r_rvalid, r_err;
  logic [31:0] r_addr, r_wdata, r_rdata;
  logic [ 3:0] r_be;
  logic        a_req, a_we, a_gnt, a_rvalid, a_err;
  logic [31:0] a_addr, a_wdata, a_rdata;
  logic [ 3:0] a_be;
  logic        k_req, k_we, k_gnt, k_rvalid, k_err;
  logic [31:0] k_addr, k_wdata, k_rdata;
  logic [ 3:0] k_be;
  // accelerator master -> RAM port B
  logic        m_req, m_we, m_gnt, m_rvalid, m_err;
  logic [31:0] m_addr, m_wdata, m_rdata;
  logic [ 3:0] m_be;

  /* verilator lint_off PINCONNECTEMPTY */
  picorv32 #(
      .ENABLE_COUNTERS(1), .ENABLE_COUNTERS64(1), .ENABLE_REGS_16_31(1),
      .ENABLE_REGS_DUALPORT(1), .LATCHED_MEM_RDATA(0), .TWO_STAGE_SHIFT(1),
      .BARREL_SHIFTER(1), .TWO_CYCLE_COMPARE(0), .TWO_CYCLE_ALU(0), .COMPRESSED_ISA(0),
      .CATCH_MISALIGN(1), .CATCH_ILLINSN(1), .ENABLE_PCPI(0), .ENABLE_MUL(0),
      .ENABLE_FAST_MUL(1), .ENABLE_DIV(1), .ENABLE_IRQ(0), .ENABLE_TRACE(0),
      .PROGADDR_RESET(32'h8000_0000), .STACKADDR(32'h8000_0000 + 32'(RAM_BYTES))
  ) u_cpu (
      .clk(clk), .resetn(!rst), .trap(cpu_trap),
      .mem_valid(mem_valid), .mem_instr(mem_instr), .mem_ready(mem_ready),
      .mem_addr(mem_addr), .mem_wdata(mem_wdata), .mem_wstrb(mem_wstrb), .mem_rdata(mem_rdata),
      .mem_la_read(), .mem_la_write(), .mem_la_addr(), .mem_la_wdata(), .mem_la_wstrb(),
      .pcpi_valid(), .pcpi_insn(), .pcpi_rs1(), .pcpi_rs2(),
      .pcpi_wr(1'b0), .pcpi_rd(32'd0), .pcpi_wait(1'b0), .pcpi_ready(1'b0),
      .irq(32'd0), .eoi(), .trace_valid(), .trace_data());
  /* verilator lint_on PINCONNECTEMPTY */

  soc_picorv32_adapter u_adapter (
      .clk(clk), .rst(rst),
      .mem_valid(mem_valid), .mem_instr(mem_instr), .mem_ready(mem_ready),
      .mem_addr(mem_addr), .mem_wdata(mem_wdata), .mem_wstrb(mem_wstrb), .mem_rdata(mem_rdata),
      .m_req(c_req), .m_addr(c_addr), .m_we(c_we), .m_be(c_be), .m_wdata(c_wdata),
      .m_gnt(c_gnt), .m_rvalid(c_rvalid), .m_rdata(c_rdata), .m_err(c_err),
      .bus_err(cpu_bus_err));

  soc_interconnect #(.RAM_BYTES(RAM_BYTES)) u_ic (
      .clk(clk), .rst(rst),
      .c_req(c_req), .c_addr(c_addr), .c_we(c_we), .c_be(c_be), .c_wdata(c_wdata),
      .c_gnt(c_gnt), .c_rvalid(c_rvalid), .c_rdata(c_rdata), .c_err(c_err),
      .r_req(r_req), .r_addr(r_addr), .r_we(r_we), .r_be(r_be), .r_wdata(r_wdata),
      .r_gnt(r_gnt), .r_rvalid(r_rvalid), .r_rdata(r_rdata), .r_err(r_err),
      .a_req(a_req), .a_addr(a_addr), .a_we(a_we), .a_be(a_be), .a_wdata(a_wdata),
      .a_gnt(a_gnt), .a_rvalid(a_rvalid), .a_rdata(a_rdata), .a_err(a_err),
      .k_req(k_req), .k_addr(k_addr), .k_we(k_we), .k_be(k_be), .k_wdata(k_wdata),
      .k_gnt(k_gnt), .k_rvalid(k_rvalid), .k_rdata(k_rdata), .k_err(k_err));

  soc_ram #(.BYTES(RAM_BYTES), .INIT_FILE(RAM_INIT)) u_ram (
      .clk(clk), .rst(rst),
      .a_req(r_req), .a_addr(r_addr), .a_we(r_we), .a_be(r_be), .a_wdata(r_wdata),
      .a_gnt(r_gnt), .a_rvalid(r_rvalid), .a_rdata(r_rdata), .a_err(r_err),
      .b_req(m_req), .b_addr(m_addr), .b_we(m_we), .b_be(m_be), .b_wdata(m_wdata),
      .b_gnt(m_gnt), .b_rvalid(m_rvalid), .b_rdata(m_rdata), .b_err(m_err));

  soc_console u_console (
      .clk(clk), .rst(rst),
      .s_req(k_req), .s_addr(k_addr), .s_we(k_we), .s_be(k_be), .s_wdata(k_wdata),
      .s_gnt(k_gnt), .s_rvalid(k_rvalid), .s_rdata(k_rdata), .s_err(k_err),
      .putc_valid(putc_valid), .putc_data(putc_data),
      .tohost_valid(tohost_valid), .tohost_data(tohost_data));

  mcuaccel_top #(.P(P)) u_accel (
      .clk(clk), .rst(rst),
      .s_req(a_req), .s_addr(a_addr), .s_we(a_we), .s_be(a_be), .s_wdata(a_wdata),
      .s_gnt(a_gnt), .s_rvalid(a_rvalid), .s_rdata(a_rdata), .s_err(a_err),
      .m_req(m_req), .m_addr(m_addr), .m_we(m_we), .m_be(m_be), .m_wdata(m_wdata),
      .m_gnt(m_gnt), .m_rvalid(m_rvalid), .m_rdata(m_rdata), .m_err(m_err),
      .busy(accel_busy));
endmodule
