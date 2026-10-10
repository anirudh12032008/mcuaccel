// WALKING-SKELETON STUB (SPEC C4 exempt): replaced in M2.
// Real port list (DESIGN 8.1). On START: BUSY for exactly one cycle, then DONE. No descriptor
// fetch, no datapath; the master port is idle.
module mcuaccel_top #(
    parameter int P = 8
) (
    input  logic        clk,
    input  logic        rst,
    input  logic        s_req,
    input  logic [31:0] s_addr,
    input  logic        s_we,
    input  logic [3:0]  s_be,
    input  logic [31:0] s_wdata,
    output logic        s_gnt,
    output logic        s_rvalid,
    output logic [31:0] s_rdata,
    output logic        s_err,
    output logic        m_req,
    output logic [31:0] m_addr,
    output logic        m_we,
    output logic [3:0]  m_be,
    output logic [31:0] m_wdata,
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic        m_gnt,
    input  logic        m_rvalid,
    input  logic [31:0] m_rdata,
    input  logic        m_err,
    /* verilator lint_on UNUSEDSIGNAL */
    output logic        busy
);
  logic start, run;
  logic [31:0] desc_addr;

  assign busy    = run;
  assign m_req   = 1'b0;
  assign m_addr  = 32'd0;
  assign m_we    = 1'b0;
  assign m_be    = 4'h0;
  assign m_wdata = 32'd0;

  always_ff @(posedge clk) begin
    if (rst) run <= 1'b0;
    else run <= start;
  end

  mcuaccel_regs #(.P(P)) u_regs (
      .clk(clk), .rst(rst),
      .s_req(s_req), .s_addr(s_addr), .s_we(s_we), .s_be(s_be), .s_wdata(s_wdata),
      .s_gnt(s_gnt), .s_rvalid(s_rvalid), .s_rdata(s_rdata), .s_err(s_err),
      .start(start), .desc_addr(desc_addr),
      .busy(run), .cmd_done(run), .chain_done(run), .err(1'b0), .err_addr(32'd0));

  /* verilator lint_off UNUSEDSIGNAL */
  logic [31:0] unused_desc;
  /* verilator lint_on UNUSEDSIGNAL */
  assign unused_desc = desc_addr;
endmodule
