// Accelerator bus master (DESIGN 8.4): the only master. One outstanding request (bus rule 3),
// writes have priority over reads, a raised request is held until granted (rule 2).
// `idle` is combinational: no response still to come and no request held.
module mcuaccel_fetch (
    input  logic        clk,
    input  logic        rst,
    input  logic        rd_valid,
    input  logic [31:0] rd_addr,
    input  logic [19:0] rd_tag,
    output logic        rd_accept,
    input  logic        wr_valid,
    input  logic [31:0] wr_addr,
    input  logic [3:0]  wr_be,
    input  logic [31:0] wr_data,
    output logic        wr_accept,
    output logic        resp_valid,
    output logic        resp_we,
    output logic        resp_err,
    output logic [31:0] resp_rdata,
    output logic [31:0] resp_addr,
    output logic [19:0] resp_tag,
    output logic        idle,
    output logic        m_req,
    output logic [31:0] m_addr,
    output logic        m_we,
    output logic [3:0]  m_be,
    output logic [31:0] m_wdata,
    input  logic        m_gnt,
    input  logic        m_rvalid,
    input  logic [31:0] m_rdata,
    input  logic        m_err
);
  logic outstanding, hold, hold_wr;
  logic can, sel_wr, accept;

  // no new request in the cycle an error response is delivered: nothing is accessed after a fault
  assign can       = !outstanding || (m_rvalid && !m_err);
  assign sel_wr    = hold ? hold_wr : wr_valid;
  assign m_req     = !rst && (hold || (can && (wr_valid || rd_valid)));
  assign m_we      = sel_wr;
  assign m_addr    = sel_wr ? wr_addr : rd_addr;
  assign m_be      = sel_wr ? wr_be : 4'hF;
  assign m_wdata   = sel_wr ? wr_data : 32'd0;
  assign accept    = m_req && m_gnt;
  assign wr_accept = accept && sel_wr;
  assign rd_accept = accept && !sel_wr;

  assign resp_valid = m_rvalid;
  assign resp_rdata = m_rdata;
  assign resp_err   = m_err;
  assign idle       = !(outstanding && !m_rvalid) && !hold;

  always_ff @(posedge clk) begin
    if (rst) begin
      outstanding <= 1'b0;
      hold        <= 1'b0;
      hold_wr     <= 1'b0;
    end else begin
      hold    <= m_req && !m_gnt;
      hold_wr <= sel_wr;
      if (accept) outstanding <= 1'b1;
      else if (m_rvalid) outstanding <= 1'b0;
    end
  end

  always_ff @(posedge clk) begin
    if (accept) begin
      resp_tag  <= rd_tag;
      resp_we   <= sel_wr;
      resp_addr <= m_addr;
    end
  end
endmodule
