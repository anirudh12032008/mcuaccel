// Requantizer (DESIGN 8.10): bias add, gemmlowp SRDHM + RDBPOT, y_zp, signed clamp.
// Two 2-deep FIFOs (tile info, acc vectors); drains 1 value/cycle; 6-stage pipeline.
// All stages freeze when !out_ready.
module mcuaccel_requant #(
    parameter int P = 8
) (
    input  logic                        clk,
    input  logic                        rst,
    input  logic                        acc_valid,
    input  logic [32*((P > 4) ? P : 4)-1:0] acc_in,
    input  logic                        tile_push,
    input  logic [8:0]                  tile_ch0,
    input  logic [4:0]                  tile_nv,
    input  logic [31:0]                 tile_yaddr,
    input  logic [7:0]                  y_zp,
    input  logic [7:0]                  act_min,
    input  logic [7:0]                  act_max,
    output logic                        pb_re,
    output logic [8:0]                  pb_raddr,
    input  logic [31:0]                 pb_bias,
    input  logic [31:0]                 pb_mult,
    input  logic [5:0]                  pb_shift,
    output logic                        out_valid,
    output logic                        out_last,
    output logic [31:0]                 out_addr,
    output logic [7:0]                  out_byte,
    input  logic                        out_ready,
    output logic                        credit_ret,
    output logic                        idle
);
  localparam int NA = (P > 4) ? P : 4;

  // ---- FIFOs (depth 2)
  logic [8:0]       t_ch0 [0:1];
  logic [4:0]       t_nv  [0:1];
  logic [31:0]      t_ya  [0:1];
  logic [32*NA-1:0] a_vec [0:1];
  logic             t_wp, t_rp, a_wp, a_rp;
  logic [1:0]       t_cnt, a_cnt;
  logic [4:0]       idx;

  wire        adv     = out_ready;
  wire        head_ok = (t_cnt != 2'd0) && (a_cnt != 2'd0);
  wire        issue   = head_ok && adv;
  wire [4:0]  nv_h    = t_nv[t_rp];
  wire        last_i  = (idx == nv_h - 5'd1);
  wire [32*NA-1:0] a_head = a_vec[a_rp];
  wire        pop     = issue && last_i;
  assign credit_ret = pop;   // DESIGN 7.2: usable by the ctrl in the cycle after the pop

  assign pb_re    = issue;
  assign pb_raddr = t_ch0[t_rp] + {4'd0, idx};

  always_ff @(posedge clk) begin
    if (tile_push) begin
      t_ch0[t_wp] <= tile_ch0;
      t_nv[t_wp]  <= tile_nv;
      t_ya[t_wp]  <= tile_yaddr;
    end
    if (acc_valid) a_vec[a_wp] <= acc_in;
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      t_wp <= 1'b0; t_rp <= 1'b0; a_wp <= 1'b0; a_rp <= 1'b0;
      t_cnt <= 2'd0; a_cnt <= 2'd0; idx <= 5'd0;
    end else begin
      if (tile_push) t_wp <= !t_wp;
      if (acc_valid) a_wp <= !a_wp;
      t_cnt <= t_cnt + {1'b0, tile_push} - {1'b0, pop};
      a_cnt <= a_cnt + {1'b0, acc_valid} - {1'b0, pop};
      if (pop) begin
        t_rp <= !t_rp;
        a_rp <= !a_rp;
        idx  <= 5'd0;
      end else if (issue) begin
        idx <= idx + 5'd1;
      end
    end
  end

  // ---- A: S0 registers (pbuf read issued this cycle, data arrives next cycle)
  logic        a_v, a_last;
  logic [31:0] a_acc, a_addr;
  // ---- B: after bias / left shift
  logic        b_v, b_last;
  logic [31:0] b_av, b_mult, b_addr;
  logic [5:0]  b_rs;
  // ---- C: product
  logic        c_v, c_last, c_sp;
  logic [63:0] c_prod;
  logic [5:0]  c_rs;
  logic [31:0] c_addr;
  // ---- D: SRDHM result
  logic        d_v, d_last;
  logic [31:0] d_x, d_addr;
  logic [5:0]  d_rs;
  // ---- E: rounded shift
  logic        e_v, e_last;
  logic [32:0] e_r;
  logic [31:0] e_addr;

  // S1 combinational
  logic [31:0] s1_s, s1_av;
  logic [5:0]  s1_rs;
  always_comb begin
    s1_s = a_acc + pb_bias;
    if (pb_shift[5]) begin
      s1_av = s1_s;
      s1_rs = 6'd0 - pb_shift;
    end else begin
      s1_av = s1_s << pb_shift[4:0];
      s1_rs = 6'd0;
    end
  end

  // S3 combinational
  wire [31:0] s3_x = 32'((c_prod + 64'h0000_0000_4000_0000) >> 31);  // bits [62:31]

  // S4 combinational
  logic signed [32:0] s4_x33;
  logic signed [32:0] s4_sh;
  logic [32:0]        s4_mask, s4_xm, s4_thr;
  always_comb begin
    s4_x33  = {d_x[31], d_x};
    s4_sh   = s4_x33 >>> d_rs;
    s4_mask = (33'd1 << d_rs) - 33'd1;
    s4_xm   = {d_x[31], d_x} & s4_mask;
    s4_thr  = (s4_mask >> 1) + {32'd0, d_x[31]};
  end

  // S5 combinational
  logic signed [32:0] s5_v;
  logic [7:0]         s5_y;
  always_comb begin
    s5_v = e_r + {{25{y_zp[7]}}, y_zp};
    if (s5_v < $signed({{25{act_min[7]}}, act_min})) s5_y = act_min;
    else if (s5_v > $signed({{25{act_max[7]}}, act_max})) s5_y = act_max;
    else s5_y = s5_v[7:0];
  end

  always_ff @(posedge clk) begin
    if (adv) begin
      a_acc  <= a_head[32*idx[3:0]+:32];
      a_addr <= t_ya[t_rp] + {27'd0, idx};
      a_last <= last_i;

      b_av   <= s1_av;
      b_mult <= pb_mult;
      b_rs   <= s1_rs;
      b_addr <= a_addr;
      b_last <= a_last;

      c_prod <= $signed(b_av) * $signed(b_mult);
      c_sp   <= (b_av == 32'h8000_0000) && (b_mult == 32'h8000_0000);
      c_rs   <= b_rs;
      c_addr <= b_addr;
      c_last <= b_last;

      d_x    <= c_sp ? 32'h7FFF_FFFF : s3_x;
      d_rs   <= c_rs;
      d_addr <= c_addr;
      d_last <= c_last;

      e_r    <= s4_sh + {32'd0, s4_xm > s4_thr};
      e_addr <= d_addr;
      e_last <= d_last;

      out_byte <= s5_y;
      out_addr <= e_addr;
      out_last <= e_last;
    end
  end

  always_ff @(posedge clk) begin
    if (rst) begin
      a_v <= 1'b0; b_v <= 1'b0; c_v <= 1'b0; d_v <= 1'b0; e_v <= 1'b0; out_valid <= 1'b0;
    end else if (adv) begin
      a_v       <= head_ok;
      b_v       <= a_v;
      c_v       <= b_v;
      d_v       <= c_v;
      e_v       <= d_v;
      out_valid <= e_v;
    end
  end

  assign idle = (t_cnt == 2'd0) && (a_cnt == 2'd0) && !a_v && !b_v && !c_v && !d_v && !e_v
                && !out_valid;
endmodule
