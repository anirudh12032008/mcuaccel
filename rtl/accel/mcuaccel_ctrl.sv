// Sequencer (DESIGN 8.5, 6.3, 7.1). One command at a time: DESC -> WLOAD -> PLOAD -> per pixel
// PSETUP (1 cycle) + bubble-free ISSUE -> FLUSH -> STAMP. All X requests come from registers
// updated on the accept of their predecessor; the per-pixel quantities (borders, mul4 products,
// start addresses) are produced by a 3-stage pipeline (counters -> A -> B -> C) that runs one
// pixel ahead and shifts at every PSETUP, so PSETUP only copies registers (one cycle, normative).
module mcuaccel_ctrl #(
    parameter int P = 8
) (
    input  logic        clk,
    input  logic        rst,
    input  logic        start,
    input  logic [31:0] desc_addr,
    output logic        busy,
    output logic        cmd_done,
    output logic        chain_done,
    output logic        err,
    output logic [31:0] err_addr,
    output logic        rd_valid,
    output logic [31:0] rd_addr,
    output logic [19:0] rd_tag,
    input  logic        rd_accept,
    output logic        st_valid,
    output logic [31:0] st_addr,
    output logic [31:0] st_data,
    input  logic        st_accept,
    input  logic        resp_valid,
    input  logic        resp_we,
    input  logic        resp_err,
    input  logic [31:0] resp_rdata,
    input  logic [31:0] resp_addr,
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic [19:0] resp_tag,
    /* verilator lint_on UNUSEDSIGNAL */
    input  logic        fetch_idle,
    input  logic        mac_idle,
    input  logic        rq_idle,
    input  logic        wr_idle,
    output logic        dw,
    output logic [7:0]  x_zp,
    output logic [7:0]  y_zp,
    output logic [7:0]  act_min,
    output logic [7:0]  act_max,
    output logic        tile_push,
    output logic [8:0]  tile_ch0,
    output logic [4:0]  tile_nv,
    output logic [31:0] tile_yaddr,
    input  logic        credit_ret,
    output logic        dp_abort        // ABORT state: top resets the datapath and gates the writer
);
  localparam logic [3:0] S_IDLE = 4'd0, S_DESC = 4'd1, S_DESC_WAIT = 4'd2, S_WLOAD = 4'd3,
                         S_WLOAD_WAIT = 4'd4, S_PLOAD = 4'd5, S_PLOAD_WAIT = 4'd6, S_PSETUP = 4'd7,
                         S_ISSUE = 4'd8, S_FLUSH = 4'd9, S_STAMP = 4'd10, S_STAMP_WAIT = 4'd11,
                         S_ABORT = 4'd12;
  localparam int STEPCH = P;

  logic [3:0] state;

  // ------------------------------------------------------------------ descriptor (words 0..16)
  logic [16:0][31:0] dsc;
  logic [31:0] daddr, stamp_a, cnt;

  logic [3:0]  kh, kw, sh, sw, ph, pw;
  logic [15:0] h_in, w_in, oh, ow, c_in, nch;
  logic [9:0]  rq, rpt, wrows, stride;
  logic [31:0] xorg, xpix, xrow, xsw, xsh, ybase, ypix, wbase, pbase;

  assign dw      = dsc[1][0];
  assign kh      = dsc[1][7:4];
  assign kw      = dsc[1][11:8];
  assign sh      = dsc[1][15:12];
  assign sw      = dsc[1][19:16];
  assign ph      = dsc[1][23:20];
  assign pw      = dsc[1][27:24];
  assign h_in    = dsc[2][15:0];
  assign w_in    = dsc[2][31:16];
  assign oh      = dsc[3][15:0];
  assign ow      = dsc[3][31:16];
  assign c_in    = dsc[4][15:0];
  assign nch     = dsc[4][31:16];
  assign rq      = dsc[5][25:16];
  assign xorg    = dsc[6];
  assign xpix    = dsc[7];
  assign xrow    = dsc[8];
  assign xsw     = dsc[9];
  assign xsh     = dsc[10];
  assign ybase   = dsc[11];
  assign ypix    = dsc[12];
  assign wbase   = dsc[13];
  assign wrows   = dsc[14][9:0];
  assign rpt     = dsc[14][25:16];
  assign pbase   = dsc[15];
  assign x_zp    = dsc[16][7:0];
  assign y_zp    = dsc[16][15:8];
  assign act_min = dsc[16][23:16];
  assign act_max = dsc[16][31:24];
  assign stride  = dw ? {6'd0, kw} : rq;

  // free-running copies of per-command constants (stable long before use)
  logic [31:0] xrow4;
  logic [4:0]  nv0;
  always_ff @(posedge clk) begin
    xrow4 <= xrow + (dw ? 32'd4 : 32'd0);
    nv0   <= dw ? 5'd4 : ((nch >= 16'(P)) ? 5'(P) : nch[4:0]);
  end

  always_ff @(posedge clk) begin
    if (resp_valid && !resp_we && !resp_err && resp_tag[1:0] == 2'd0) begin
      for (int i = 0; i < 17; i++) begin
        if (resp_tag[6:2] == 5'(i)) dsc[i] <= resp_rdata;
      end
    end
  end

  // ------------------------------------------------------------------ helpers
  function automatic logic [31:0] mul4(input logic [3:0] k, input logic [31:0] v);
    logic [31:0] t0, t1, t2, t3;
    begin
      t0   = k[0] ? v : 32'd0;
      t1   = k[1] ? (v << 1) : 32'd0;
      t2   = k[2] ? (v << 2) : 32'd0;
      t3   = k[3] ? (v << 3) : 32'd0;
      mul4 = (t0 + t1) + (t2 + t3);
    end
  endfunction

  // border clipping of one axis: {n, k1, k0} = {k1 - k0 (or 0), min(K, ext - i0), max(0, -i0)}
  function automatic logic [11:0] border(input logic [20:0] i0, input logic [15:0] ext,
                                         input logic [3:0] k);
    logic [20:0] k0f;
    logic signed [21:0] hm;
    logic [3:0] k0, k1, n;
    begin
      k0f = i0[20] ? (21'd0 - i0) : 21'd0;
      k0  = (k0f > 21'd15) ? 4'd15 : k0f[3:0];
      hm  = $signed({6'd0, ext}) - $signed({i0[20], i0});
      if (hm <= 22'sd0) k1 = 4'd0;
      else if (hm >= $signed({18'd0, k})) k1 = k;
      else k1 = hm[3:0];
      n      = (k1 > k0) ? (k1 - k0) : 4'd0;
      border = {n, k1, k0};
    end
  endfunction

  // ------------------------------------------------------------------ pixel pipeline
  // counters: the next pixel to enter stage A
  logic [15:0] pc_oy, pc_ox;
  logic [20:0] pc_iy0, pc_ix0;
  logic [31:0] pc_pixa, pc_pixr, pc_yp;
  logic        pc_last;
  logic [1:0]  fc;           // fill shifts done before the first PSETUP (needs 3)
  logic        shift;

  assign pc_last = (pc_ox == ow - 16'd1) && (pc_oy == oh - 16'd1);

  // stage A (from counters)
  /* verilator lint_off UNUSEDSIGNAL */
  logic [11:0] bdy, bdx;
  logic [31:0] m_b1, m_wr0;
  logic [18:0] nq19;
  logic [20:0] b1p;
  /* verilator lint_on UNUSEDSIGNAL */
  logic [3:0]  s_ky0, s_nky, s_kx0, s_kx1, s_nkx;
  assign bdy   = border(pc_iy0, h_in, kh);
  assign bdx   = border(pc_ix0, w_in, kw);
  assign s_ky0 = bdy[3:0];
  assign s_nky = bdy[11:8];
  assign s_kx0 = bdx[3:0];
  assign s_kx1 = bdx[7:4];
  assign s_nkx = bdx[11:8];

  logic [31:0] a_pixa, a_yp, a_prow, a_koff;
  assign m_b1  = mul4(s_kx1, {16'd0, c_in});
  assign m_wr0 = mul4(s_ky0, {22'd0, stride});
  logic [19:0] a_b1;
  logic [9:0]  a_wr0;
  logic [3:0]  a_kx0, a_nkx, a_nky;
  logic        a_last;

  // stage B (from A)
  logic [31:0] b_sf_n, b_row0;
  logic [18:0] j0, j1;
  logic [10:0] b_nq_n;
  logic [3:0]  b_mf_n, b_ml_n;
  logic [9:0]  b_wr0_n;
  logic        b_empty_n;
  assign b1p       = {1'b0, a_b1} + 21'd3;
  assign j0        = a_koff[20:2];
  assign j1        = b1p[20:2];
  assign nq19      = (j1 > j0) ? (j1 - j0) : 19'd0;
  assign b_nq_n    = dw ? {7'd0, a_nkx} : nq19[10:0];
  assign b_row0    = a_pixa + a_prow;
  assign b_sf_n    = b_row0 + (dw ? a_koff : {12'd0, a_koff[19:2], 2'b00});
  assign b_wr0_n   = a_wr0 + (dw ? {6'd0, a_kx0} : j0[9:0]);
  assign b_mf_n    = dw ? 4'hF : (4'hF << a_koff[1:0]);
  always_comb begin
    b_ml_n = 4'hF;
    if (!dw) begin
      case (a_b1[1:0])
        2'd1:    b_ml_n = 4'b0001;
        2'd2:    b_ml_n = 4'b0011;
        2'd3:    b_ml_n = 4'b0111;
        default: b_ml_n = 4'b1111;
      endcase
    end
  end
  assign b_empty_n = (a_nky == 4'd0) || (b_nq_n == 11'd0);

  logic [31:0] b_sf, b_yp;
  logic [10:0] b_nq;
  logic [3:0]  b_mf, b_ml, b_nky;
  logic [9:0]  b_wr0;
  logic        b_empty, b_last;

  // stage C (from B)
  logic [31:0] c_sf, c_sf1, c_sfn, c_sfn1, c_yp;
  logic [10:0] c_nq;
  logic [3:0]  c_mf, c_ml, c_nky;
  logic [9:0]  c_wr0;
  logic        c_empty, c_last;

  always_ff @(posedge clk) begin
    if (shift) begin
      a_pixa  <= pc_pixa;
      a_yp    <= pc_yp;
      a_last  <= pc_last;
      a_prow  <= mul4(s_ky0, xrow);
      a_koff  <= mul4(s_kx0, dw ? xpix : {16'd0, c_in});
      a_b1    <= m_b1[19:0];
      a_wr0   <= m_wr0[9:0];
      a_kx0   <= s_kx0;
      a_nkx   <= s_nkx;
      a_nky   <= s_nky;

      b_sf    <= b_sf_n;
      b_yp    <= a_yp;
      b_last  <= a_last;
      b_nq    <= b_nq_n;
      b_mf    <= b_mf_n;
      b_ml    <= b_ml_n;
      b_nky   <= a_nky;
      b_wr0   <= b_wr0_n;
      b_empty <= b_empty_n;

      c_sf    <= b_sf;
      c_sf1   <= b_sf + xrow;
      c_sfn   <= b_sf + (dw ? 32'd4 : 32'd0);
      c_sfn1  <= b_sf + xrow4;
      c_yp    <= b_yp;
      c_last  <= b_last;
      c_nq    <= b_nq;
      c_mf    <= b_mf;
      c_ml    <= b_ml;
      c_nky   <= b_nky;
      c_wr0   <= b_wr0;
      c_empty <= b_empty;
    end
  end

  // ------------------------------------------------------------------ request engine registers
  logic [31:0] ra, snext, sfn, sfn1;
  logic [1:0]  ra_a;
  logic        pr, fst;
  logic [10:0] jleft;
  logic [3:0]  kyrem;
  logic [9:0]  wrow, wrj0, wt0;
  logic [3:0]  msk;
  logic [9:0]  ch0;
  logic [4:0]  nv;
  logic [10:0] w_nq;
  logic [3:0]  w_nky, w_mf, w_ml;
  logic [31:0] w_yp;
  logic        w_last;
  logic [9:0]  ld_i;
  logic [4:0]  ld_j;
  logic [1:0]  credit;
  logic        fl_settle;
  logic        cur_last, rowend, lastrow, moretile, lastacc, errnow;
  logic [9:0]  ch0n;

  assign cur_last = !pr && (jleft == 11'd0) && (kyrem == 4'd0);
  assign rowend   = !pr && (jleft == 11'd0);
  assign lastrow  = (kyrem == 4'd0);
  assign ch0n     = ch0 + 10'(dw ? 4 : STEPCH);
  assign moretile = ({6'd0, ch0n} < nch);
  assign lastacc  = (state == S_ISSUE) && rd_accept && cur_last;
  assign errnow   = resp_valid && resp_err && (state != S_IDLE) && (state != S_ABORT);

  logic [15:0] rem_n;
  logic [4:0]  nv_next;
  assign rem_n   = nch - {6'd0, ch0n};
  assign nv_next = dw ? 5'd4 : ((rem_n >= 16'(P)) ? 5'(P) : rem_n[4:0]);

  // row / tile / pixel start loads
  logic       rl_prime, tl_prime, ps_prime;
  logic [1:0] rl_a, tl_a, ps_a;
  logic [10:0] rl_jleft, tl_jleft, ps_jleft;
  logic [3:0] rl_msk, ps_msk;
  assign rl_a     = dw ? 2'd0 : snext[1:0];
  assign tl_a     = dw ? 2'd0 : sfn[1:0];
  assign ps_a     = dw ? 2'd0 : c_sf[1:0];
  assign rl_prime = rl_a != 2'd0;
  assign tl_prime = tl_a != 2'd0;
  assign ps_prime = ps_a != 2'd0;
  assign rl_jleft = rl_prime ? w_nq : w_nq - 11'd1;
  assign tl_jleft = tl_prime ? w_nq : w_nq - 11'd1;
  assign ps_jleft = ps_prime ? c_nq : c_nq - 11'd1;
  assign rl_msk   = w_mf & ((w_nq == 11'd1) ? w_ml : 4'hF);
  assign ps_msk   = c_mf & ((c_nq == 11'd1) ? c_ml : 4'hF);

  // ------------------------------------------------------------------ request outputs
  logic wzero, pzero, nopix, fill_state;
  assign wzero = wrows == 10'd0;
  assign pzero = nch == 16'd0;
  assign nopix = (oh == 16'd0) || (ow == 16'd0);
  assign fill_state = (state == S_WLOAD) || (state == S_WLOAD_WAIT) || (state == S_PLOAD) ||
                      (state == S_PLOAD_WAIT) || (state == S_PSETUP);
  assign shift = (fill_state && fc != 2'd3) || (state == S_PSETUP && fc == 2'd3);

  always_comb begin
    rd_valid = 1'b0;
    rd_tag   = 20'd0;
    case (state)
      S_DESC: begin
        rd_valid = 1'b1;
        rd_tag   = {13'd0, ld_i[4:0], 2'd0};
      end
      S_WLOAD: begin
        rd_valid = !wzero;
        rd_tag   = {5'd0, ld_j[3:0], ld_i[8:0], 2'd1};
      end
      S_PLOAD: begin
        rd_valid = !pzero;
        rd_tag   = {7'd0, ld_j[1:0], ld_i[8:0], 2'd2};
      end
      S_ISSUE: begin
        rd_valid = !(cur_last && credit == 2'd0);
        if (pr) rd_tag = {9'd0, 1'b0, 1'b0, ra_a, 4'd0, 1'b0, 2'd3};
        else
          rd_tag = {wrow[8:0], cur_last, fst, ra_a, msk, 1'b1, 2'd3};
      end
      default: ;
    endcase
  end
  assign rd_addr    = ra;
  assign st_valid   = state == S_STAMP;
  assign st_addr    = stamp_a;
  assign st_data    = cnt;
  assign dp_abort   = state == S_ABORT;

  logic [4:0] wpw;
  assign wpw = dw ? 5'd1 : 5'(P);

  // ------------------------------------------------------------------ sequencer
  always_ff @(posedge clk) begin
    if (rst) begin
      state      <= S_IDLE;
      busy       <= 1'b0;
      cmd_done   <= 1'b0;
      chain_done <= 1'b0;
      err        <= 1'b0;
      tile_push  <= 1'b0;
      credit     <= 2'd2;
      fl_settle  <= 1'b0;
      fc         <= 2'd0;
    end else begin
      cmd_done   <= 1'b0;
      chain_done <= 1'b0;
      err        <= 1'b0;
      tile_push  <= lastacc && !errnow;
      if (state != S_ABORT) credit <= credit - 2'(lastacc) + 2'(credit_ret);
      if (chain_done || err) busy <= 1'b0;
      if (shift && fc != 2'd3) fc <= fc + 2'd1;
      if (state != S_STAMP && state != S_STAMP_WAIT) cnt <= cnt + 32'd1;
      if (shift) begin
        pc_yp <= pc_yp + ypix;
        if (pc_ox == ow - 16'd1) begin
          pc_ox   <= 16'd0;
          pc_oy   <= pc_oy + 16'd1;
          pc_ix0  <= 21'd0 - {17'd0, pw};
          pc_iy0  <= pc_iy0 + {17'd0, sh};
          pc_pixr <= pc_pixr + xsh;
          pc_pixa <= pc_pixr + xsh;
        end else begin
          pc_ox   <= pc_ox + 16'd1;
          pc_ix0  <= pc_ix0 + {17'd0, sw};
          pc_pixa <= pc_pixa + xsw;
        end
      end

      case (state)
        S_IDLE: begin
          if (start && !busy) begin
            busy  <= 1'b1;
            credit <= 2'd2;
            daddr <= desc_addr;
            ra    <= desc_addr;
            ld_i  <= 10'd0;
            cnt   <= 32'd0;
            state <= S_DESC;
          end
        end
        S_DESC: begin
          if (rd_accept) begin
            ra   <= ra + 32'd4;
            ld_i <= ld_i + 10'd1;
            if (ld_i[4:0] == 5'd16) state <= S_DESC_WAIT;
          end
        end
        S_DESC_WAIT: begin
          if (fetch_idle) begin
            // pixel counters for pixel (0,0); descriptor words 1..15 arrived long ago
            pc_oy   <= 16'd0;
            pc_ox   <= 16'd0;
            pc_iy0  <= 21'd0 - {17'd0, ph};
            pc_ix0  <= 21'd0 - {17'd0, pw};
            pc_pixa <= xorg;
            pc_pixr <= xorg;
            pc_yp   <= ybase;
            fc      <= 2'd0;
            ra      <= wbase;
            ld_i    <= 10'd0;
            ld_j    <= 5'd0;
            state   <= S_WLOAD;
          end
        end
        S_WLOAD: begin
          if (wzero) begin
            ra    <= pbase;
            ld_i  <= 10'd0;
            ld_j  <= 5'd0;
            state <= S_PLOAD;
          end else if (rd_accept) begin
            ra <= ra + 32'd4;
            if (ld_j == wpw - 5'd1) begin
              ld_j <= 5'd0;
              ld_i <= ld_i + 10'd1;
              if (ld_i + 10'd1 == wrows) state <= S_WLOAD_WAIT;
            end else begin
              ld_j <= ld_j + 5'd1;
            end
          end
        end
        S_WLOAD_WAIT: begin
          if (fetch_idle) begin
            ra    <= pbase;
            ld_i  <= 10'd0;
            ld_j  <= 5'd0;
            state <= S_PLOAD;
          end
        end
        S_PLOAD: begin
          if (pzero) begin
            if (nopix) begin
              fl_settle <= 1'b1;
              state     <= S_FLUSH;
            end else state <= S_PSETUP;
          end else if (rd_accept) begin
            ra <= ra + 32'd4;
            if (ld_j[1:0] == 2'd2) begin
              ld_j <= 5'd0;
              ld_i <= ld_i + 10'd1;
              if ({6'd0, ld_i} + 16'd1 == nch) state <= S_PLOAD_WAIT;
            end else begin
              ld_j <= ld_j + 5'd1;
            end
          end
        end
        S_PLOAD_WAIT: begin
          if (fetch_idle) begin
            if (nopix) begin
              fl_settle <= 1'b1;
              state     <= S_FLUSH;
            end else state <= S_PSETUP;
          end
        end
        S_PSETUP: begin
          if (fc == 2'd3) begin
            w_nq   <= c_nq;
            w_nky  <= c_nky;
            w_mf   <= c_mf;
            w_ml   <= c_ml;
            w_yp   <= c_yp;
            w_last <= c_last;
            if (c_empty) begin
              if (c_last) begin
                fl_settle <= 1'b1;
                state     <= S_FLUSH;
              end
            end else begin
              ra    <= {c_sf[31:2], 2'b00};
              ra_a  <= ps_a;
              pr    <= ps_prime;
              jleft <= ps_jleft;
              kyrem <= c_nky - 4'd1;
              snext <= c_sf1;
              sfn   <= c_sfn;
              sfn1  <= c_sfn1;
              wrj0  <= c_wr0;
              wt0   <= c_wr0;
              wrow  <= c_wr0;
              msk   <= ps_msk;
              fst   <= 1'b1;
              ch0   <= 10'd0;
              nv    <= nv0;
              state <= S_ISSUE;
            end
          end
        end
        S_ISSUE: begin
          if (rd_accept) begin
            if (!pr) fst <= 1'b0;
            if (!rowend) begin
              ra    <= ra + (dw ? xpix : 32'd4);
              jleft <= jleft - 11'd1;
              pr    <= 1'b0;
              wrow  <= pr ? wrj0 : wrow + 10'd1;
              msk   <= (pr ? w_mf : 4'hF) & ((jleft == 11'd1) ? w_ml : 4'hF);
            end else if (!lastrow) begin
              ra    <= {snext[31:2], 2'b00};
              ra_a  <= rl_a;
              pr    <= rl_prime;
              jleft <= rl_jleft;
              kyrem <= kyrem - 4'd1;
              snext <= snext + xrow;
              wrj0  <= wrj0 + stride;
              wrow  <= wrj0 + stride;
              msk   <= rl_msk;
            end else if (moretile) begin
              ra    <= {sfn[31:2], 2'b00};
              ra_a  <= tl_a;
              pr    <= tl_prime;
              jleft <= tl_jleft;
              kyrem <= w_nky - 4'd1;
              snext <= sfn1;
              sfn   <= sfn + (dw ? 32'd4 : 32'd0);
              sfn1  <= sfn1 + (dw ? 32'd4 : 32'd0);
              wt0   <= wt0 + rpt;
              wrj0  <= wt0 + rpt;
              wrow  <= wt0 + rpt;
              msk   <= rl_msk;
              fst   <= 1'b1;
              ch0   <= ch0n;
              nv    <= nv_next;
            end else begin
              if (w_last) begin
                fl_settle <= 1'b1;
                state     <= S_FLUSH;
              end else state <= S_PSETUP;
            end
          end
        end
        S_FLUSH: begin
          if (fl_settle) fl_settle <= 1'b0;
          else if (mac_idle && rq_idle && wr_idle && fetch_idle && credit == 2'd2) state <= S_STAMP;
        end
        S_STAMP: begin
          if (st_accept) state <= S_STAMP_WAIT;
        end
        S_STAMP_WAIT: begin
          if (fetch_idle) begin
            cmd_done <= 1'b1;
            if (dsc[0] != 32'd0) begin
              daddr <= dsc[0];
              ra    <= dsc[0];
              ld_i  <= 10'd0;
              cnt   <= 32'd0;
              state <= S_DESC;
            end else begin
              chain_done <= 1'b1;
              state      <= S_IDLE;
            end
          end
        end
        S_ABORT: begin
          if (fetch_idle) begin
            err   <= 1'b1;
            state <= S_IDLE;
          end
        end
        default: state <= S_IDLE;
      endcase

      if (errnow) begin
        state    <= S_ABORT;
        credit   <= 2'd2;
        cmd_done <= 1'b0;
        chain_done <= 1'b0;
      end
    end
  end

  always_ff @(posedge clk) begin
    stamp_a <= daddr + 32'd68;
    if (lastacc) begin
      tile_ch0   <= ch0[8:0];
      tile_nv    <= nv;
      tile_yaddr <= w_yp + {22'd0, ch0};
    end
    if (errnow) err_addr <= resp_addr;
  end
endmodule
