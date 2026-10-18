// Byte-to-word writer (DESIGN 8.11): assembles consecutive bytes into a word, 1-entry pending write.
module mcuaccel_writer (
    input  logic        clk,
    input  logic        rst,
    input  logic        in_valid,
    input  logic        in_last,
    input  logic [31:0] in_addr,
    input  logic [7:0]  in_byte,
    output logic        in_ready,
    output logic        wr_valid,
    output logic [31:0] wr_addr,
    output logic [3:0]  wr_be,
    output logic [31:0] wr_data,
    input  logic        wr_accept,
    output logic        idle
);
  logic [3:0]  asm_be;
  logic [31:0] asm_data;
  logic [3:0]  nxt_be;
  logic [31:0] nxt_data;

  assign in_ready = !wr_valid || wr_accept;
  assign idle     = !wr_valid && asm_be == 4'd0;

  always_comb begin
    nxt_be   = asm_be | (4'd1 << in_addr[1:0]);
    nxt_data = asm_data;
    nxt_data[8*in_addr[1:0]+:8] = in_byte;
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      wr_valid <= 1'b0;
      asm_be   <= 4'd0;
    end else begin
      if (wr_accept) wr_valid <= 1'b0;
      if (in_valid && in_ready) begin
        if (in_addr[1:0] == 2'd3 || in_last) begin
          wr_valid <= 1'b1;
          asm_be   <= 4'd0;
        end else begin
          asm_be <= nxt_be;
        end
      end
    end
  end

  always_ff @(posedge clk) begin
    if (in_valid && in_ready) begin
      asm_data <= nxt_data;
      if (in_addr[1:0] == 2'd3 || in_last) begin
        wr_addr <= {in_addr[31:2], 2'b00};
        wr_be   <= nxt_be;
        wr_data <= nxt_data;
      end
    end
  end
endmodule
