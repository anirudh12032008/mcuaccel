// corv32_sc: walking skeleton; replaced in M1.
// Minimal single-cycle core: LUI, ADDI, SW, JAL only. Everything else (and a misaligned SW
// address or JAL target) retires as trap+halt with no architectural effect. Ports: DESIGN 4.2.
// RVFI is registered (record for cycle t appears in t+1); halted is sticky.
module corv32_sc #(
  parameter logic [31:0] RESET_ADDR = 32'h8000_0000
) (
  input  logic        clk,
  input  logic        rst,
  output logic [31:0] imem_addr,
  input  logic [31:0] imem_rdata,
  output logic        dmem_valid,
  output logic [31:0] dmem_addr,
  output logic        dmem_we,
  output logic [3:0]  dmem_be,
  output logic [31:0] dmem_wdata,
  input  logic [31:0] dmem_rdata,
  output logic        halted
`ifdef CORV32_RVFI
  ,
  output logic        rvfi_valid,
  output logic [63:0] rvfi_order,
  output logic [31:0] rvfi_insn,
  output logic        rvfi_trap,
  output logic        rvfi_halt,
  output logic        rvfi_intr,
  output logic [1:0]  rvfi_mode,
  output logic [1:0]  rvfi_ixl,
  output logic [4:0]  rvfi_rs1_addr,
  output logic [4:0]  rvfi_rs2_addr,
  output logic [31:0] rvfi_rs1_rdata,
  output logic [31:0] rvfi_rs2_rdata,
  output logic [4:0]  rvfi_rd_addr,
  output logic [31:0] rvfi_rd_wdata,
  output logic [31:0] rvfi_pc_rdata,
  output logic [31:0] rvfi_pc_wdata,
  output logic [31:0] rvfi_mem_addr,
  output logic [3:0]  rvfi_mem_rmask,
  output logic [3:0]  rvfi_mem_wmask,
  output logic [31:0] rvfi_mem_rdata,
  output logic [31:0] rvfi_mem_wdata
`endif
);
  logic [31:0] pc;
  logic [31:0] regs [32];

  // ---- decode ----
  logic [31:0] insn;
  logic [6:0]  opcode;
  logic [2:0]  funct3;
  logic        is_lui, is_addi, is_sw, is_jal, legal;
  logic [4:0]  rs1, rs2, rd;
  logic [31:0] imm_i, imm_s, imm_u, imm_j;

  assign imem_addr = pc;
  assign insn      = imem_rdata;
  assign opcode    = insn[6:0];
  assign funct3    = insn[14:12];
  assign is_lui    = opcode == 7'b0110111;
  assign is_addi   = opcode == 7'b0010011 && funct3 == 3'b000;
  assign is_sw     = opcode == 7'b0100011 && funct3 == 3'b010;
  assign is_jal    = opcode == 7'b1101111;
  assign legal     = is_lui || is_addi || is_sw || is_jal;
  assign rs1       = (is_addi || is_sw) ? insn[19:15] : 5'd0;
  assign rs2       = is_sw ? insn[24:20] : 5'd0;
  assign rd        = insn[11:7];
  assign imm_i     = {{20{insn[31]}}, insn[31:20]};
  assign imm_s     = {{20{insn[31]}}, insn[31:25], insn[11:7]};
  assign imm_u     = {insn[31:12], 12'h0};
  assign imm_j     = {{11{insn[31]}}, insn[31], insn[19:12], insn[20], insn[30:21], 1'b0};

  // ---- execute (combinational) ----
  logic [31:0] rs1_val, rs2_val, mem_ea, jal_tgt, rd_val, next_pc;
  logic        trap, wr_rd;

  assign rs1_val  = (rs1 == 5'd0) ? 32'h0 : regs[rs1];
  assign rs2_val  = (rs2 == 5'd0) ? 32'h0 : regs[rs2];
  assign mem_ea   = rs1_val + imm_s;
  assign jal_tgt  = pc + imm_j;
  assign trap     = !legal || (is_sw && mem_ea[1:0] != 2'b00) || (is_jal && jal_tgt[1:0] != 2'b00);
  assign wr_rd    = !trap && !is_sw && rd != 5'd0;
  assign rd_val   = is_lui ? imm_u : is_jal ? pc + 32'd4 : rs1_val + imm_i;
  assign next_pc  = trap ? pc : is_jal ? jal_tgt : pc + 32'd4;

  assign dmem_valid = !halted && is_sw && !trap;
  assign dmem_we    = dmem_valid;
  assign dmem_be    = dmem_valid ? 4'hF : 4'h0;
  assign dmem_addr  = {mem_ea[31:2], 2'b00};
  assign dmem_wdata = rs2_val;

  always_ff @(posedge clk) begin
    if (rst) begin
      pc     <= RESET_ADDR;
      halted <= 1'b0;
    end else if (!halted) begin
      pc     <= next_pc;
      halted <= trap;
      if (wr_rd) regs[rd] <= rd_val;
    end
  end

`ifdef CORV32_RVFI
  always_ff @(posedge clk) begin
    rvfi_valid <= !rst && !halted;
    if (rst) rvfi_order <= 64'h0;
    else if (rvfi_valid) rvfi_order <= rvfi_order + 64'h1;
    rvfi_insn      <= insn;
    rvfi_trap      <= trap;
    rvfi_halt      <= trap;
    rvfi_intr      <= 1'b0;
    rvfi_mode      <= 2'd3;
    rvfi_ixl       <= 2'd1;
    rvfi_rs1_addr  <= rs1;
    rvfi_rs2_addr  <= rs2;
    rvfi_rs1_rdata <= rs1_val;
    rvfi_rs2_rdata <= rs2_val;
    rvfi_rd_addr   <= wr_rd ? rd : 5'd0;
    rvfi_rd_wdata  <= wr_rd ? rd_val : 32'h0;
    rvfi_pc_rdata  <= pc;
    rvfi_pc_wdata  <= next_pc;
    rvfi_mem_addr  <= dmem_valid ? dmem_addr : 32'h0;
    rvfi_mem_rmask <= 4'h0;
    rvfi_mem_wmask <= dmem_be;
    rvfi_mem_rdata <= 32'h0;
    rvfi_mem_wdata <= dmem_valid ? dmem_wdata : 32'h0;
  end
`endif

  // skeleton has no loads
  logic unused;
  assign unused = &{1'b0, dmem_rdata};
endmodule
