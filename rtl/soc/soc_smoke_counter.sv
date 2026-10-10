// Toolchain smoke test: 8-bit free-running counter.
module soc_smoke_counter (
    input  logic       clk,
    input  logic       rst,
    output logic [7:0] q
);
  always_ff @(posedge clk) begin
    if (rst) q <= 8'd0;
    else q <= q + 8'd1;
  end
endmodule
