// Unaligned-run aligner (DESIGN 8.8). prev loads on every valid word; quad is combinational.
module mcuaccel_align (
    input  logic        clk,
    input  logic        in_valid,
    input  logic [31:0] in_word,
    input  logic [1:0]  in_a,
    output logic [31:0] quad
);
  logic [23:0] prev;  // low byte of the previous word is never used

  always_ff @(posedge clk) begin
    if (in_valid) prev <= in_word[31:8];
  end

  always_comb begin
    case (in_a)
      2'd1:    quad = {in_word[7:0], prev[23:0]};
      2'd2:    quad = {in_word[15:0], prev[23:8]};
      2'd3:    quad = {in_word[23:0], prev[23:16]};
      default: quad = in_word;
    endcase
  end
endmodule
