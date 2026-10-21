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

  // Four byte-lane memories, one write enable per lane and port: the form Yosys memory_libmap
  // maps to ECP5 DP16KD (true dual port, read-first). A 32-bit array with per-byte writes on two
  // ports falls back to flip-flops. no_rw_check: a read on one port in the cycle the other port
  // writes the same word is undefined on DP16KD (simulation returns the old word); without the
  // attribute Yosys refuses the block RAM. Same-port read-first is kept (WRITEMODE READBEFOREWRITE).
  (* no_rw_check *) logic [7:0] mem0[0:WORDS-1];
  (* no_rw_check *) logic [7:0] mem1[0:WORDS-1];
  (* no_rw_check *) logic [7:0] mem2[0:WORDS-1];
  (* no_rw_check *) logic [7:0] mem3[0:WORDS-1];

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
  logic [31:0] img[0:WORDS-1];
  initial begin
    string f;
    for (int i = 0; i < WORDS; i++) img[i] = 32'h0;
    if (INIT_FILE != "") $readmemh(INIT_FILE, img);
    if ($value$plusargs("ram=%s", f)) $readmemh(f, img);
    for (int i = 0; i < WORDS; i++) begin
      mem0[i] = img[i][7:0];
      mem1[i] = img[i][15:8];
      mem2[i] = img[i][23:16];
      mem3[i] = img[i][31:24];
    end
  end
`endif

  logic a_wr, b_wr;
  assign a_wr = a_req && a_we && a_in;
  assign b_wr = b_req && b_we && b_in;

  always_ff @(posedge clk) begin
    a_rdata[7:0] <= mem0[a_idx];
    if (a_wr && a_be[0]) mem0[a_idx] <= a_wdata[7:0];
  end
  always_ff @(posedge clk) begin
    b_rdata[7:0] <= mem0[b_idx];
    if (b_wr && b_be[0]) mem0[b_idx] <= b_wdata[7:0];
  end
  always_ff @(posedge clk) begin
    a_rdata[15:8] <= mem1[a_idx];
    if (a_wr && a_be[1]) mem1[a_idx] <= a_wdata[15:8];
  end
  always_ff @(posedge clk) begin
    b_rdata[15:8] <= mem1[b_idx];
    if (b_wr && b_be[1]) mem1[b_idx] <= b_wdata[15:8];
  end
  always_ff @(posedge clk) begin
    a_rdata[23:16] <= mem2[a_idx];
    if (a_wr && a_be[2]) mem2[a_idx] <= a_wdata[23:16];
  end
  always_ff @(posedge clk) begin
    b_rdata[23:16] <= mem2[b_idx];
    if (b_wr && b_be[2]) mem2[b_idx] <= b_wdata[23:16];
  end
  always_ff @(posedge clk) begin
    a_rdata[31:24] <= mem3[a_idx];
    if (a_wr && a_be[3]) mem3[a_idx] <= a_wdata[31:24];
  end
  always_ff @(posedge clk) begin
    b_rdata[31:24] <= mem3[b_idx];
    if (b_wr && b_be[3]) mem3[b_idx] <= b_wdata[31:24];
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
