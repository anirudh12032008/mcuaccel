// MAC array (DESIGN 8.9, 7.3). S1: masked (x - x_zp) * w products, registered.
// S2: lane sum + accumulate (conv) or per-lane accumulate (dw). Accumulators wrap mod 2^32.
module mcuaccel_mac_array #(
    parameter int P = 8
) (
    input  logic                        clk,
    input  logic                        rst,
    input  logic                        in_valid,
    input  logic                        in_first,
    input  logic                        in_last,
    input  logic [31:0]                 in_quad,
    input  logic [3:0]                  in_mask,
    input  logic [7:0]                  x_zp,
    input  logic                        dw,
    input  logic [32*P-1:0]             in_w,
    output logic                        out_valid,
    output logic [32*((P > 4) ? P : 4)-1:0] out_acc,
    output logic                        idle
);
  localparam int NA = (P > 4) ? P : 4;

  // ---- S1: products
  logic [35:0]     xs;                // 4 x 9-bit signed (x - x_zp), masked lanes forced to 0
  logic [17*4*P-1:0] prod;            // [17*(4p+l) +: 17] signed
  logic            s1_v, s1_first, s1_last, s1_dw;
  logic            s2_v;

  for (genvar l = 0; l < 4; l++) begin : g_xs
    assign xs[9*l+:9] = in_mask[l] ?
        9'($signed({in_quad[8*l+7], in_quad[8*l+:8]}) - $signed({x_zp[7], x_zp})) : 9'd0;
  end

  for (genvar p = 0; p < P; p++) begin : g_p
    for (genvar l = 0; l < 4; l++) begin : g_l
      always_ff @(posedge clk) begin
        prod[17*(4*p+l)+:17] <= 17'($signed(xs[9*l+:9]) * $signed(in_w[32*p+8*l+:8]));
      end
    end
  end

  always_ff @(posedge clk) begin
    s1_first <= in_first;
    s1_last  <= in_last;
    s1_dw    <= dw;
  end

  // ---- S2: accumulate
  logic [32*NA-1:0] acc;

  for (genvar i = 0; i < NA; i++) begin : g_acc
    logic [31:0] conv_inc, dw_inc, inc;
    if (i < P) begin : g_conv
      logic [18:0] sum;
      always_comb begin
        sum = 19'd0;
        for (int l = 0; l < 4; l++) sum = sum + {{2{prod[17*(4*i+l)+16]}}, prod[17*(4*i+l)+:17]};
      end
      assign conv_inc = {{13{sum[18]}}, sum};
    end else begin : g_noconv
      assign conv_inc = 32'd0;
    end
    if (i < 4) begin : g_dw
      assign dw_inc = {{15{prod[17*i+16]}}, prod[17*i+:17]};
    end else begin : g_nodw
      assign dw_inc = 32'd0;
    end
    assign inc = s1_dw ? dw_inc : conv_inc;
    always_ff @(posedge clk) begin
      if (s1_v) acc[32*i+:32] <= s1_first ? inc : acc[32*i+:32] + inc;
    end
  end

  assign out_acc = acc;
  assign idle    = !(s1_v || s2_v);

  always_ff @(posedge clk) begin
    if (rst) begin
      s1_v      <= 1'b0;
      s2_v      <= 1'b0;
      out_valid <= 1'b0;
    end else begin
      s1_v      <= in_valid;
      s2_v      <= s1_v;
      out_valid <= s1_v && s1_last;
    end
  end
endmodule
