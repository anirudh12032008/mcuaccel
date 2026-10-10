// Synthesis wrapper (DESIGN 10.5): one register on every input and output of mcuaccel_top,
// no logic, so out-of-context timing is FF->FF and excludes I/O. `rst` is registered too.
module mcuaccel_top_wrap #(
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
    input  logic        m_gnt,
    input  logic        m_rvalid,
    input  logic [31:0] m_rdata,
    input  logic        m_err,
    output logic        busy
);
  logic        rst_q, s_req_q, s_we_q, m_gnt_q, m_rvalid_q, m_err_q;
  logic [31:0] s_addr_q, s_wdata_q, m_rdata_q;
  logic [3:0]  s_be_q;
  logic        c_s_gnt, c_s_rvalid, c_s_err, c_m_req, c_m_we, c_busy;
  logic [31:0] c_s_rdata, c_m_addr, c_m_wdata;
  logic [3:0]  c_m_be;

  always_ff @(posedge clk) begin
    rst_q      <= rst;
    s_req_q    <= s_req;
    s_addr_q   <= s_addr;
    s_we_q     <= s_we;
    s_be_q     <= s_be;
    s_wdata_q  <= s_wdata;
    m_gnt_q    <= m_gnt;
    m_rvalid_q <= m_rvalid;
    m_rdata_q  <= m_rdata;
    m_err_q    <= m_err;
    s_gnt      <= c_s_gnt;
    s_rvalid   <= c_s_rvalid;
    s_rdata    <= c_s_rdata;
    s_err      <= c_s_err;
    m_req      <= c_m_req;
    m_addr     <= c_m_addr;
    m_we       <= c_m_we;
    m_be       <= c_m_be;
    m_wdata    <= c_m_wdata;
    busy       <= c_busy;
  end

  mcuaccel_top #(.P(P)) u_dut (
      .clk(clk), .rst(rst_q),
      .s_req(s_req_q), .s_addr(s_addr_q), .s_we(s_we_q), .s_be(s_be_q), .s_wdata(s_wdata_q),
      .s_gnt(c_s_gnt), .s_rvalid(c_s_rvalid), .s_rdata(c_s_rdata), .s_err(c_s_err),
      .m_req(c_m_req), .m_addr(c_m_addr), .m_we(c_m_we), .m_be(c_m_be), .m_wdata(c_m_wdata),
      .m_gnt(m_gnt_q), .m_rvalid(m_rvalid_q), .m_rdata(m_rdata_q), .m_err(m_err_q),
      .busy(c_busy));
endmodule
