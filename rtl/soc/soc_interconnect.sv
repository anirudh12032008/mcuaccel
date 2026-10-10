// CPU-side bus decode (DESIGN 4.1/4.3): RAM, accelerator regs, console, error responder.
module soc_interconnect #(
    parameter int RAM_BYTES = 1048576
) (
    input  logic        clk,
    input  logic        rst,
    input  logic        c_req,
    input  logic [31:0] c_addr,
    input  logic        c_we,
    input  logic [3:0]  c_be,
    input  logic [31:0] c_wdata,
    output logic        c_gnt,
    output logic        c_rvalid,
    output logic [31:0] c_rdata,
    output logic        c_err,
    // RAM port A
    output logic        r_req,
    output logic [31:0] r_addr,
    output logic        r_we,
    output logic [3:0]  r_be,
    output logic [31:0] r_wdata,
    input  logic        r_gnt,
    input  logic        r_rvalid,
    input  logic [31:0] r_rdata,
    input  logic        r_err,
    // accelerator registers
    output logic        a_req,
    output logic [31:0] a_addr,
    output logic        a_we,
    output logic [3:0]  a_be,
    output logic [31:0] a_wdata,
    input  logic        a_gnt,
    input  logic        a_rvalid,
    input  logic [31:0] a_rdata,
    input  logic        a_err,
    // console
    output logic        k_req,
    output logic [31:0] k_addr,
    output logic        k_we,
    output logic [3:0]  k_be,
    output logic [31:0] k_wdata,
    input  logic        k_gnt,
    input  logic        k_rvalid,
    input  logic [31:0] k_rdata,
    input  logic        k_err
);
  logic [31:0] ram_off;
  logic sel_r, sel_a, sel_k, sel_e;
  logic [1:0] idx;  // latched: 0 ram, 1 accel, 2 console, 3 error
  logic e_rvalid;

  assign ram_off = c_addr - 32'h8000_0000;
  assign sel_r = ram_off < 32'(RAM_BYTES);
  assign sel_a = c_addr[31:12] == 20'h30000;
  assign sel_k = c_addr[31:8] == 24'h100000;
  assign sel_e = !(sel_r || sel_a || sel_k);

  assign r_req = c_req && sel_r;
  assign a_req = c_req && sel_a;
  assign k_req = c_req && sel_k;
  assign {r_addr, a_addr, k_addr} = {3{c_addr}};
  assign {r_we, a_we, k_we} = {3{c_we}};
  assign {r_be, a_be, k_be} = {3{c_be}};
  assign {r_wdata, a_wdata, k_wdata} = {3{c_wdata}};

  assign c_gnt = sel_r ? r_gnt : sel_a ? a_gnt : sel_k ? k_gnt : 1'b1;

  always_ff @(posedge clk) begin
    if (rst) begin
      e_rvalid <= 1'b0;
      idx      <= 2'd0;
    end else begin
      e_rvalid <= c_req && sel_e;
      if (c_req && c_gnt) idx <= sel_r ? 2'd0 : sel_a ? 2'd1 : sel_k ? 2'd2 : 2'd3;
    end
  end

  always_comb begin
    case (idx)
      2'd0: {c_rvalid, c_rdata, c_err} = {r_rvalid, r_rdata, r_err};
      2'd1: {c_rvalid, c_rdata, c_err} = {a_rvalid, a_rdata, a_err};
      2'd2: {c_rvalid, c_rdata, c_err} = {k_rvalid, k_rdata, k_err};
      default: {c_rvalid, c_rdata, c_err} = {e_rvalid, 32'd0, 1'b1};
    endcase
  end
endmodule
