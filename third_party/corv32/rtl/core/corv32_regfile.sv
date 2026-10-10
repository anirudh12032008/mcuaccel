// 32x32 register file: two async read ports, one sync write port, x0 hardwired to zero.
// No reset (DESIGN section 0); infers distributed RAM (LUTRAM) on ECP5.
// WRITE_THROUGH=1: a same-cycle write to the read address is bypassed to the read data.
module corv32_regfile #(parameter bit WRITE_THROUGH = 1'b1) (
  input  logic        clk,
  input  logic [4:0]  ra1, output logic [31:0] rd1,
  input  logic [4:0]  ra2, output logic [31:0] rd2,
  input  logic        we,  input  logic [4:0]  wa, input logic [31:0] wd
);
  logic [31:0] mem [0:31];

  always_ff @(posedge clk) begin
    if (we && wa != 5'd0) mem[wa] <= wd;
  end

  always_comb begin
    rd1 = mem[ra1];
    rd2 = mem[ra2];
    if (WRITE_THROUGH && we && wa == ra1) rd1 = wd;
    if (WRITE_THROUGH && we && wa == ra2) rd2 = wd;
    if (ra1 == 5'd0) rd1 = 32'd0;
    if (ra2 == 5'd0) rd2 = 32'd0;
  end
endmodule
