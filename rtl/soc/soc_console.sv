// Simulation console (DESIGN 4.2): PUTCHAR, TOHOST, 64-bit cycle counter. Zero-wait slave.
module soc_console (
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
    output logic        putc_valid,
    output logic [7:0]  putc_data,
    output logic        tohost_valid,
    output logic [31:0] tohost_data
);
  logic [63:0] cycles;
  logic wr;
  logic [5:0] off;  // word offset in the 256 B window
  assign off = s_addr[7:2];
  /* verilator lint_off UNUSEDSIGNAL */
  logic [25:0] unused_addr;
  /* verilator lint_on UNUSEDSIGNAL */
  assign unused_addr = {s_addr[31:8], s_addr[1:0]};
  assign s_gnt = 1'b1;
  assign s_err = 1'b0;
  assign wr = s_req && s_we;

  always_ff @(posedge clk) begin
    if (rst) begin
      cycles       <= 64'd0;
      s_rvalid     <= 1'b0;
      putc_valid   <= 1'b0;
      tohost_valid <= 1'b0;
    end else begin
      cycles       <= cycles + 64'd1;
      s_rvalid     <= s_req;
      putc_valid   <= wr && off == 6'd0 && s_be[0];
      tohost_valid <= wr && off == 6'd1 && s_be == 4'hF;
    end
    // data registers are not reset (valid qualifies them)
    if (wr && off == 6'd0) putc_data <= s_wdata[7:0];
    if (wr && off == 6'd1) tohost_data <= s_wdata;
    case (off)
      6'd2:    s_rdata <= cycles[31:0];
      6'd3:    s_rdata <= cycles[63:32];
      default: s_rdata <= 32'd0;
    endcase
  end
endmodule
