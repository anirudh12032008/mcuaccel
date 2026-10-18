// Accelerator top (DESIGN 8.1): register slave, one bus master (fetch), sequencer, datapath.
module mcuaccel_top #(
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
    output logic        m_req,
    output logic [31:0] m_addr,
    output logic        m_we,
    output logic [3:0]  m_be,
    output logic [31:0] m_wdata,
    input  logic        m_gnt,
    input  logic        m_rvalid,
    input  logic [31:0] m_rdata,
    input  logic        m_err,
    output logic        busy
);
  localparam int NACC = (P > 4) ? P : 4;

  // regs <-> ctrl
  logic        start, cmd_done, chain_done, err;
  logic [31:0] desc_addr, err_addr;

  // ctrl <-> fetch
  logic        rd_valid, rd_accept, st_valid, st_accept;
  logic [31:0] rd_addr, st_addr, st_data;
  logic [19:0] rd_tag;
  logic        resp_valid, resp_we, resp_err, fetch_idle;
  logic [31:0] resp_rdata, resp_addr;
  logic [19:0] resp_tag;

  // writer <-> fetch
  logic        w_valid, w_valid_g, w_accept, wr_valid, wr_accept;
  logic [31:0] w_addr, w_data;
  logic [3:0]  w_be;

  // ctrl -> datapath
  logic        dw, dp_abort, dp_rst;
  logic [7:0]  x_zp, y_zp, act_min, act_max;
  logic        tile_push, credit_ret;
  logic [8:0]  tile_ch0;
  logic [4:0]  tile_nv;
  logic [31:0] tile_yaddr;
  logic        mac_idle, rq_idle, wr_idle;

  assign dp_rst = rst || dp_abort;

  // ---------------------------------------------------------------- response routing by tag
  logic        resp_ok, r_wbuf, r_pbuf, r_x, r_mac;
  assign resp_ok = resp_valid && !resp_we && !resp_err;
  assign r_wbuf  = resp_ok && resp_tag[1:0] == 2'd1;
  assign r_pbuf  = resp_ok && resp_tag[1:0] == 2'd2;
  assign r_x     = resp_ok && resp_tag[1:0] == 2'd3;
  assign r_mac   = r_x && resp_tag[2];

  // ---------------------------------------------------------------- write port: writer | stamp
  assign wr_valid  = st_valid || w_valid_g;
  assign w_valid_g = w_valid && !dp_abort;
  assign st_accept = wr_accept && st_valid;
  assign w_accept  = wr_accept && !st_valid;

  mcuaccel_regs #(.P(P)) u_regs (
      .clk(clk), .rst(rst),
      .s_req(s_req), .s_addr(s_addr), .s_we(s_we), .s_be(s_be), .s_wdata(s_wdata),
      .s_gnt(s_gnt), .s_rvalid(s_rvalid), .s_rdata(s_rdata), .s_err(s_err),
      .start(start), .desc_addr(desc_addr),
      .busy(busy), .cmd_done(cmd_done), .chain_done(chain_done), .err(err), .err_addr(err_addr));

  mcuaccel_fetch u_fetch (
      .clk(clk), .rst(rst),
      .rd_valid(rd_valid), .rd_addr(rd_addr), .rd_tag(rd_tag), .rd_accept(rd_accept),
      .wr_valid(wr_valid), .wr_addr(st_valid ? st_addr : w_addr),
      .wr_be(st_valid ? 4'hF : w_be), .wr_data(st_valid ? st_data : w_data),
      .wr_accept(wr_accept),
      .resp_valid(resp_valid), .resp_we(resp_we), .resp_err(resp_err),
      .resp_rdata(resp_rdata), .resp_addr(resp_addr), .resp_tag(resp_tag), .idle(fetch_idle),
      .m_req(m_req), .m_addr(m_addr), .m_we(m_we), .m_be(m_be), .m_wdata(m_wdata),
      .m_gnt(m_gnt), .m_rvalid(m_rvalid), .m_rdata(m_rdata), .m_err(m_err));

  mcuaccel_ctrl #(.P(P)) u_ctrl (
      .clk(clk), .rst(rst), .start(start), .desc_addr(desc_addr),
      .busy(busy), .cmd_done(cmd_done), .chain_done(chain_done), .err(err), .err_addr(err_addr),
      .rd_valid(rd_valid), .rd_addr(rd_addr), .rd_tag(rd_tag), .rd_accept(rd_accept),
      .st_valid(st_valid), .st_addr(st_addr), .st_data(st_data), .st_accept(st_accept),
      .resp_valid(resp_valid), .resp_we(resp_we), .resp_err(resp_err),
      .resp_rdata(resp_rdata), .resp_addr(resp_addr), .resp_tag(resp_tag),
      .fetch_idle(fetch_idle), .mac_idle(mac_idle), .rq_idle(rq_idle), .wr_idle(wr_idle),
      .dw(dw), .x_zp(x_zp), .y_zp(y_zp), .act_min(act_min), .act_max(act_max),
      .tile_push(tile_push), .tile_ch0(tile_ch0), .tile_nv(tile_nv), .tile_yaddr(tile_yaddr),
      .credit_ret(credit_ret), .dp_abort(dp_abort));

  // ---------------------------------------------------------------- buffers
  logic [32*P-1:0] wbuf_rdata;
  logic            wbuf_re;
  assign wbuf_re = rd_accept && rd_tag[1:0] == 2'd3 && rd_tag[2];

  mcuaccel_wbuf #(.P(P)) u_wbuf (
      .clk(clk), .we(r_wbuf), .waddr(resp_tag[10:2]), .wword(resp_tag[14:11]),
      .wdata(resp_rdata), .re(wbuf_re), .raddr(rd_tag[19:11]), .rdata(wbuf_rdata));

  logic        pb_re;
  logic [8:0]  pb_raddr;
  logic [31:0] pb_bias, pb_mult;
  logic [5:0]  pb_shift;

  mcuaccel_pbuf u_pbuf (
      .clk(clk), .we(r_pbuf), .waddr(resp_tag[10:2]), .wsel(resp_tag[12:11]),
      .wdata(resp_rdata), .re(pb_re), .raddr(pb_raddr),
      .bias(pb_bias), .mult(pb_mult), .shift(pb_shift));

  // ---------------------------------------------------------------- datapath
  logic [31:0] quad;
  mcuaccel_align u_align (
      .clk(clk), .in_valid(r_x), .in_word(resp_rdata), .in_a(resp_tag[8:7]), .quad(quad));

  logic                 mac_valid;
  logic [32*NACC-1:0]   mac_acc;
  mcuaccel_mac_array #(.P(P)) u_mac (
      .clk(clk), .rst(dp_rst),
      .in_valid(r_mac), .in_first(resp_tag[9]), .in_last(resp_tag[10]), .in_quad(quad),
      .in_mask(resp_tag[6:3]), .x_zp(x_zp), .dw(dw), .in_w(wbuf_rdata),
      .out_valid(mac_valid), .out_acc(mac_acc), .idle(mac_idle));

  logic        rq_valid, rq_last, rq_ready;
  logic [31:0] rq_addr;
  logic [7:0]  rq_byte;
  mcuaccel_requant #(.P(P)) u_requant (
      .clk(clk), .rst(dp_rst),
      .acc_valid(mac_valid), .acc_in(mac_acc),
      .tile_push(tile_push), .tile_ch0(tile_ch0), .tile_nv(tile_nv), .tile_yaddr(tile_yaddr),
      .y_zp(y_zp), .act_min(act_min), .act_max(act_max),
      .pb_re(pb_re), .pb_raddr(pb_raddr), .pb_bias(pb_bias), .pb_mult(pb_mult),
      .pb_shift(pb_shift),
      .out_valid(rq_valid), .out_last(rq_last), .out_addr(rq_addr), .out_byte(rq_byte),
      .out_ready(rq_ready), .credit_ret(credit_ret), .idle(rq_idle));

  mcuaccel_writer u_writer (
      .clk(clk), .rst(dp_rst),
      .in_valid(rq_valid), .in_last(rq_last), .in_addr(rq_addr), .in_byte(rq_byte),
      .in_ready(rq_ready),
      .wr_valid(w_valid), .wr_addr(w_addr), .wr_be(w_be), .wr_data(w_data),
      .wr_accept(w_accept), .idle(wr_idle));
endmodule
