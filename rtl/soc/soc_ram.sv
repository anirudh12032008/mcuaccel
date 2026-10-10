// True dual-port word RAM (DESIGN 4.3). Port A: CPU, port B: accelerator. Zero-wait, read-first.
module soc_ram #(
    parameter int BYTES = 1048576,
    parameter logic [31:0] BASE = 32'h8000_0000,
    parameter INIT_FILE = ""
) (
    input  logic        clk,
    input  logic        rst,
    input  logic        a_req,
    input  logic [31:0] a_addr,
    input  logic        a_we,
    input  logic [3:0]  a_be,
    input  logic [31:0] a_wdata,
    output logic        a_gnt,
    output logic        a_rvalid,
    output logic [31:0] a_rdata,
    output logic        a_err,
    input  logic        b_req,
    input  logic [31:0] b_addr,
    input  logic        b_we,
    input  logic [3:0]  b_be,
    input  logic [31:0] b_wdata,
    output logic        b_gnt,
    output logic        b_rvalid,
    output logic [31:0] b_rdata,
    output logic        b_err
);
  localparam int WORDS = BYTES / 4;
  localparam int AW = $clog2(WORDS);

  logic [31:0] mem[0:WORDS-1];

  /* verilator lint_off UNUSEDSIGNAL */
  logic [31:0] a_off, b_off;
  /* verilator lint_on UNUSEDSIGNAL */
  logic a_in, b_in;
  logic [AW-1:0] a_idx, b_idx;
  assign a_off = a_addr - BASE;
  assign b_off = b_addr - BASE;
  assign a_in  = a_off < 32'(BYTES);
  assign b_in  = b_off < 32'(BYTES);
  assign a_idx = a_off[AW+1:2];
  assign b_idx = b_off[AW+1:2];
  assign a_gnt = 1'b1;
  assign b_gnt = 1'b1;

`ifndef SYNTHESIS
  initial begin
    string f;
    if (INIT_FILE != "") $readmemh(INIT_FILE, mem);
    if ($value$plusargs("ram=%s", f)) $readmemh(f, mem);
  end
`endif

  always_ff @(posedge clk) begin
    a_rdata <= mem[a_idx];
    b_rdata <= mem[b_idx];
    for (int k = 0; k < 4; k++) begin
      if (a_req && a_we && a_in && a_be[k]) mem[a_idx][8*k+:8] <= a_wdata[8*k+:8];
      if (b_req && b_we && b_in && b_be[k]) mem[b_idx][8*k+:8] <= b_wdata[8*k+:8];
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      a_rvalid <= 1'b0;
      b_rvalid <= 1'b0;
      a_err    <= 1'b0;
      b_err    <= 1'b0;
    end else begin
      a_rvalid <= a_req;
      b_rvalid <= b_req;
      a_err    <= a_req && !a_in;
      b_err    <= b_req && !b_in;
    end
  end
endmodule
