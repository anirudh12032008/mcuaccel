// Register file (DESIGN 6.1 / 8.3). Zero-wait slave at 0x3000_0000.
module mcuaccel_regs #(
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
    output logic        start,
    output logic [31:0] desc_addr,
    input  logic        busy,
    input  logic        cmd_done,
    input  logic        chain_done,
    input  logic        err,
    input  logic [31:0] err_addr
);
  logic [31:0] cycles, ncmd, err_addr_q;
  logic done, errf, wr, clear;
  /* verilator lint_off UNUSEDSIGNAL */
  logic [21:0] unused_addr;
  /* verilator lint_on UNUSEDSIGNAL */
  assign unused_addr = {s_addr[31:12], s_addr[1:0]};

  logic [9:0] off;  // word offset within the 4 KiB window; only 0..7 are registers
  assign off   = s_addr[11:2];
  assign s_gnt = 1'b1;
  assign s_err = 1'b0;
  assign wr    = s_req && s_we && s_be == 4'hF;
  assign start = wr && off == 10'd2 && s_wdata[0] && !busy;
  assign clear = wr && off == 10'd2 && s_wdata[1];

  always_ff @(posedge clk) begin
    if (rst) begin
      s_rvalid  <= 1'b0;
      desc_addr <= 32'd0;
      done      <= 1'b0;
      errf      <= 1'b0;
      cycles    <= 32'd0;
      ncmd      <= 32'd0;
      s_rdata   <= 32'd0;
    end else begin
      s_rvalid <= s_req;
      if (wr && off == 10'd4) desc_addr <= s_wdata;
      if (start || clear) begin
        done <= 1'b0;
        errf <= 1'b0;
      end
      if (chain_done) done <= 1'b1;
      if (err) errf <= 1'b1;
      if (start) begin
        cycles <= 32'd0;
        ncmd   <= 32'd0;
      end else begin
        if (busy) cycles <= cycles + 32'd1;
        if (cmd_done) ncmd <= ncmd + 32'd1;
      end
      case (off)
        10'd0:    s_rdata <= 32'h4D43_4131;
        10'd1:    s_rdata <= {16'(512), 8'(4), 8'(P)};
        10'd3:    s_rdata <= {29'd0, errf, done, busy};
        10'd4:    s_rdata <= desc_addr;
        10'd5:    s_rdata <= cycles;
        10'd6:    s_rdata <= ncmd;
        10'd7:    s_rdata <= err_addr_q;
        default: s_rdata <= 32'd0;
      endcase
    end
  end

  always_ff @(posedge clk) begin
    if (rst) err_addr_q <= 32'd0;
    else if (err) err_addr_q <= err_addr;
  end
endmodule
