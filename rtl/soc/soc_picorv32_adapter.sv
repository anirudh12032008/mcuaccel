// PicoRV32 native memory interface -> corv32 bus master (DESIGN 4.3).
module soc_picorv32_adapter (
    input  logic        clk,
    input  logic        rst,
    input  logic        mem_valid,
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic        mem_instr,
    input  logic [31:0] mem_addr,
    /* verilator lint_on UNUSEDSIGNAL */
    output logic        mem_ready,
    input  logic [31:0] mem_wdata,
    input  logic [3:0]  mem_wstrb,
    output logic [31:0] mem_rdata,
    output logic        m_req,
    output logic [31:0] m_addr,
    output logic        m_we,
    output logic [3:0]  m_be,
    output logic [31:0] m_wdata,
    input  logic        m_gnt,
    input  logic        m_rvalid,
    input  logic [31:0] m_rdata,
    input  logic        m_err,
    output logic        bus_err
);
  logic pend;

  assign m_req     = mem_valid && !pend;
  assign m_addr    = {mem_addr[31:2], 2'b00};
  assign m_we      = |mem_wstrb;
  assign m_be      = m_we ? mem_wstrb : 4'hF;
  assign m_wdata   = mem_wdata;
  assign mem_ready = m_rvalid;
  assign mem_rdata = m_rdata;

  always_ff @(posedge clk) begin
    if (rst) begin
      pend    <= 1'b0;
      bus_err <= 1'b0;
    end else begin
      if (m_rvalid) pend <= 1'b0;
      else if (m_req && m_gnt) pend <= 1'b1;
      if (m_rvalid && m_err) bus_err <= 1'b1;
    end
  end
endmodule
