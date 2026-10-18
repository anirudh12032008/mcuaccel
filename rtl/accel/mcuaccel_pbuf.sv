// Per-channel requant parameters (DESIGN 8.7): bias, mult, shift; synchronous read, held when !re.
module mcuaccel_pbuf (
    input  logic        clk,
    input  logic        we,
    input  logic [8:0]  waddr,
    input  logic [1:0]  wsel,
    input  logic [31:0] wdata,
    input  logic        re,
    input  logic [8:0]  raddr,
    output logic [31:0] bias,
    output logic [31:0] mult,
    output logic [5:0]  shift
);
  logic [31:0] m_bias  [0:511];
  logic [31:0] m_mult  [0:511];
  logic [5:0]  m_shift [0:511];

  always_ff @(posedge clk) begin
    if (we && wsel == 2'd0) m_bias[waddr] <= wdata;
    if (re) bias <= m_bias[raddr];
  end
  always_ff @(posedge clk) begin
    if (we && wsel == 2'd1) m_mult[waddr] <= wdata;
    if (re) mult <= m_mult[raddr];
  end
  always_ff @(posedge clk) begin
    if (we && wsel == 2'd2) m_shift[waddr] <= wdata[5:0];
    if (re) shift <= m_shift[raddr];
  end
endmodule
