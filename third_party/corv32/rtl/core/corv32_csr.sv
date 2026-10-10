// corv32_csr: machine-mode CSR file, trap/mret state, interrupt gating, 64-bit counters.
// DESIGN 6.7, SPEC 4.2/4.5. Unimplemented CSRs (including pmp*, tselect) are illegal.
module corv32_csr (
  input  logic        clk, rst,
  input  logic        acc_valid,
  input  logic [11:0] acc_addr,
  input  logic [1:0]  acc_op,       // 01 RW, 10 RS, 11 RC
  input  logic        acc_wr,
  input  logic [31:0] acc_src,
  output logic [31:0] acc_rdata,
  output logic [31:0] acc_wdata,
  output logic        acc_illegal,
  input  logic        trap_take,
  input  logic [31:0] trap_cause,
  input  logic [31:0] trap_epc,
  input  logic [31:0] trap_tval,
  output logic [31:0] trap_vector,
  input  logic        mret_take,
  output logic [31:0] mepc_o,
  input  logic        irq_external, irq_timer, irq_software,
  output logic        irq_pending,
  output logic [31:0] irq_cause,
  input  logic        instret_inc,
  input  logic        instret_dec,
  input  logic [63:0] mtime,
  output logic [63:0] mcycle_o, minstret_o
);
  logic        mie_q, mpie_q;            // mstatus.MIE / MPIE
  logic        mie_e, mie_t, mie_s;      // mie.MEIE / MTIE / MSIE
  logic [31:2] mtvec_base;
  logic        mtvec_mode;
  logic [31:0] mscratch, mcause, mtval;
  logic [31:2] mepc;                     // bits 1:0 are unobservable (IALIGN=32)
  logic        mip_e, mip_t, mip_s;      // registered interrupt inputs
  logic [63:0] mcycle, minstret;

  assign mcycle_o   = mcycle;
  assign minstret_o = minstret;
  assign mepc_o     = {mepc, 2'b00};

  logic unused;  // epc bits 1:0 are not stored (IALIGN=32)
  assign unused = &{1'b0, trap_epc[1:0]};

  // ---------------------------------------------------------------- read mux
  logic legal;
  always_comb begin
    acc_rdata = 32'b0;
    legal     = 1'b1;
    case (acc_addr)
      12'h300: acc_rdata = {19'b0, 2'b11, 3'b0, mpie_q, 3'b0, mie_q, 3'b0};
      12'h301: acc_rdata = 32'h4000_1100;
      12'h304: acc_rdata = {20'b0, mie_e, 3'b0, mie_t, 3'b0, mie_s, 3'b0};
      12'h305: acc_rdata = {mtvec_base, 1'b0, mtvec_mode};
      12'h310, 12'h320: acc_rdata = 32'b0;
      12'h340: acc_rdata = mscratch;
      12'h341: acc_rdata = {mepc, 2'b00};
      12'h342: acc_rdata = mcause;
      12'h343: acc_rdata = mtval;
      12'h344: acc_rdata = {20'b0, mip_e, 3'b0, mip_t, 3'b0, mip_s, 3'b0};
      12'hB00, 12'hC00: acc_rdata = mcycle[31:0];
      12'hB80, 12'hC80: acc_rdata = mcycle[63:32];
      12'hB02, 12'hC02: acc_rdata = minstret[31:0];
      12'hB82, 12'hC82: acc_rdata = minstret[63:32];
      12'hC01: acc_rdata = mtime[31:0];
      12'hC81: acc_rdata = mtime[63:32];
      default: begin
        // mhpmevent3-31, mhpmcounter3-31(h), mvendorid..mconfigptr read as zero
        if (!((acc_addr >= 12'h323 && acc_addr <= 12'h33F) ||
              (acc_addr >= 12'hB03 && acc_addr <= 12'hB1F) ||
              (acc_addr >= 12'hB83 && acc_addr <= 12'hB9F) ||
              (acc_addr >= 12'hF11 && acc_addr <= 12'hF15)))
          legal = 1'b0;
      end
    endcase
  end

  assign acc_illegal = !legal || (acc_wr && acc_addr[11:10] == 2'b11);

  always_comb begin
    case (acc_op)
      2'b01:   acc_wdata = acc_src;
      2'b10:   acc_wdata = acc_rdata | acc_src;
      default: acc_wdata = acc_rdata & ~acc_src;
    endcase
  end

  // ---------------------------------------------------------------- trap vector / irq
  assign trap_vector = (mtvec_mode && trap_cause[31])
                     ? {mtvec_base, 2'b00} + {25'b0, trap_cause[4:0], 2'b00}
                     : {mtvec_base, 2'b00};

  logic ip_e, ip_t, ip_s;
  assign ip_e        = mip_e && mie_e;
  assign ip_t        = mip_t && mie_t;
  assign ip_s        = mip_s && mie_s;
  assign irq_pending = mie_q && (ip_e || ip_t || ip_s);
  assign irq_cause   = ip_e ? 32'h8000_000B : ip_s ? 32'h8000_0003 : 32'h8000_0007;

  // ---------------------------------------------------------------- state
  logic wr, wr_cycle, wr_instret;
  assign wr         = acc_valid && acc_wr && !acc_illegal;
  assign wr_cycle   = wr && (acc_addr == 12'hB00 || acc_addr == 12'hB80);
  assign wr_instret = wr && (acc_addr == 12'hB02 || acc_addr == 12'hB82);

  always_ff @(posedge clk) begin
    mip_e <= irq_external;
    mip_t <= irq_timer;
    mip_s <= irq_software;
    if (rst) begin
      mie_q <= 1'b0; mpie_q <= 1'b0;
      mie_e <= 1'b0; mie_t <= 1'b0; mie_s <= 1'b0;
      mtvec_base <= '0; mtvec_mode <= 1'b0;
      mscratch <= 32'b0; mcause <= 32'b0; mtval <= 32'b0; mepc <= '0;
      mip_e <= 1'b0; mip_t <= 1'b0; mip_s <= 1'b0;
      mcycle <= 64'b0; minstret <= 64'b0;
    end else begin
      if (trap_take) begin
        mepc <= trap_epc[31:2]; mcause <= trap_cause; mtval <= trap_tval;
        mpie_q <= mie_q; mie_q <= 1'b0;
      end else if (mret_take) begin
        mie_q <= mpie_q; mpie_q <= 1'b1;
      end
      if (wr) begin
        case (acc_addr)
          12'h300: begin mie_q <= acc_wdata[3]; mpie_q <= acc_wdata[7]; end
          12'h304: begin mie_e <= acc_wdata[11]; mie_t <= acc_wdata[7]; mie_s <= acc_wdata[3]; end
          12'h305: begin mtvec_base <= acc_wdata[31:2]; mtvec_mode <= acc_wdata[0]; end
          12'h340: mscratch <= acc_wdata;
          12'h341: mepc <= acc_wdata[31:2];
          12'h342: mcause <= acc_wdata;
          12'h343: mtval <= acc_wdata;
          12'hB00: mcycle[31:0] <= acc_wdata;
          12'hB80: mcycle[63:32] <= acc_wdata;
          12'hB02: minstret[31:0] <= acc_wdata;
          12'hB82: minstret[63:32] <= acc_wdata;
          default: ;
        endcase
      end
      if (!wr_cycle) mcycle <= mcycle + 64'd1;
      if (!wr_instret) begin
        if (instret_inc && !instret_dec) minstret <= minstret + 64'd1;
        else if (instret_dec && !instret_inc) minstret <= minstret - 64'd1;
      end
    end
  end
endmodule
