// Weight buffer (DESIGN 8.6): P x (512 x 32) memories, synchronous read, output held when !re.
module mcuaccel_wbuf #(
    parameter int P = 8
) (
    input  logic            clk,
    input  logic            we,
    input  logic [8:0]      waddr,
    input  logic [3:0]      wword,
    input  logic [31:0]     wdata,
    input  logic            re,
    input  logic [8:0]      raddr,
    output logic [32*P-1:0] rdata
);
  for (genvar p = 0; p < P; p++) begin : g_mem
    logic [31:0] m [0:511];
    logic [31:0] q;
    always_ff @(posedge clk) begin
      if (we && wword == 4'(p)) m[waddr] <= wdata;
      if (re) q <= m[raddr];
    end
    assign rdata[32*p+:32] = q;
  end
endmodule
