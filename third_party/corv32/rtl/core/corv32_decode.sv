// corv32_decode - combinational RV32IM/Zicsr/Zifencei instruction decoder (DESIGN 6.2).
// Illegal words: illegal=1, every is_* and rs1/rs2/rd address = 0 (the control fields are
// zeroed too); funct3, csr_addr and zimm are always raw instruction bits.
module corv32_decode #(
  parameter bit HAS_M     = 1'b1,
  parameter bit HAS_ZICSR = 1'b1
) (
  input  logic [31:0] instr,
  output logic        illegal,
  output logic [4:0]  rs1_addr,    // 0 if rs1 not read (incl. CSR*I, LUI, AUIPC, JAL)
  output logic [4:0]  rs2_addr,    // 0 if rs2 not read (only R-type, branches, stores read it)
  output logic [4:0]  rd_addr,     // 0 if rd not written (branches, stores, system, fence)
  output logic [31:0] imm,         // I/S/B/U/J immediate per format, sign-extended
  output logic [3:0]  alu_op,      // corv32_alu op; I-type uses {instr[30] for SRAI only, funct3}
  output logic [1:0]  alu_a_sel,   // 0 rs1, 1 pc (AUIPC), 2 zero (LUI)
  output logic        alu_b_imm,   // 1: b = imm, 0: b = rs2
  output logic [2:0]  res_sel,     // 0 ALU, 1 PC+4 (JAL/JALR), 2 LOAD, 3 CSR, 4 MULDIV
  output logic        is_branch, is_jal, is_jalr,
  output logic        is_load, is_store,
  output logic [1:0]  mem_size,    // 0 byte, 1 half, 2 word
  output logic        mem_unsigned,// LBU/LHU
  output logic        is_mul,      // MUL, MULH, MULHSU, MULHU
  output logic        is_div,      // DIV, DIVU, REM, REMU
  output logic [2:0]  funct3,      // raw instr[14:12]
  output logic        is_csr,
  output logic [11:0] csr_addr,    // instr[31:20]
  output logic        csr_imm,     // funct3[2]
  output logic        csr_wr,      // write attempted: funct3[1:0]==01, or instr[19:15]!=0
  output logic [4:0]  zimm,        // instr[19:15]
  output logic        is_ecall, is_ebreak, is_mret, is_wfi, is_fence, is_fencei
);
  `include "corv32_defs.svh"

  localparam logic [4:0] OPC_LOAD   = 5'b00000;  // instr[6:2]; instr[1:0] must be 2'b11
  localparam logic [4:0] OPC_FENCE  = 5'b00011;
  localparam logic [4:0] OPC_OPIMM  = 5'b00100;
  localparam logic [4:0] OPC_AUIPC  = 5'b00101;
  localparam logic [4:0] OPC_STORE  = 5'b01000;
  localparam logic [4:0] OPC_OP     = 5'b01100;
  localparam logic [4:0] OPC_LUI    = 5'b01101;
  localparam logic [4:0] OPC_BRANCH = 5'b11000;
  localparam logic [4:0] OPC_JALR   = 5'b11001;
  localparam logic [4:0] OPC_JAL    = 5'b11011;
  localparam logic [4:0] OPC_SYSTEM = 5'b11100;

  localparam logic [31:0] W_ECALL  = 32'h0000_0073;
  localparam logic [31:0] W_EBREAK = 32'h0010_0073;
  localparam logic [31:0] W_MRET   = 32'h3020_0073;
  localparam logic [31:0] W_WFI    = 32'h1050_0073;

  logic [4:0] opc;
  logic [6:0] f7;
  logic [2:0] f3;
  logic       ok;       // legal word
  logic       md;       // legal OP word with funct7 = 0000001

  logic [31:0] imm_i, imm_s, imm_b, imm_u, imm_j;

  assign opc = instr[6:2];
  assign f7  = instr[31:25];
  assign f3  = instr[14:12];

  assign imm_i = {{20{instr[31]}}, instr[31:20]};
  assign imm_s = {{20{instr[31]}}, instr[31:25], instr[11:7]};
  assign imm_b = {{19{instr[31]}}, instr[31], instr[7], instr[30:25], instr[11:8], 1'b0};
  assign imm_u = {instr[31:12], 12'b0};
  assign imm_j = {{11{instr[31]}}, instr[31], instr[19:12], instr[20], instr[30:21], 1'b0};

  // Legality: the DESIGN 6.2 legal set
  always_comb begin
    ok = 1'b0;
    md = 1'b0;
    if (instr[1:0] == 2'b11) begin
      case (opc)
        OPC_LUI, OPC_AUIPC, OPC_JAL: ok = 1'b1;
        OPC_JALR:   ok = (f3 == 3'b000);
        OPC_BRANCH: ok = (f3 != 3'b010) && (f3 != 3'b011);
        OPC_LOAD:   ok = (f3 == 3'b000) || (f3 == 3'b001) || (f3 == 3'b010) ||
                         (f3 == 3'b100) || (f3 == 3'b101);
        OPC_STORE:  ok = (f3 == 3'b000) || (f3 == 3'b001) || (f3 == 3'b010);
        OPC_OPIMM:  begin
          if (f3 == 3'b001)      ok = (f7 == 7'b0000000);
          else if (f3 == 3'b101) ok = (f7 == 7'b0000000) || (f7 == 7'b0100000);
          else                   ok = 1'b1;
        end
        OPC_OP: begin
          md = HAS_M && (f7 == 7'b0000001);
          ok = (f7 == 7'b0000000) ||
               (f7 == 7'b0100000 && (f3 == 3'b000 || f3 == 3'b101)) || md;
        end
        OPC_FENCE:  ok = (f3 == 3'b000) || (f3 == 3'b001);
        OPC_SYSTEM: begin
          if (f3 == 3'b000)
            ok = (instr == W_ECALL) || (instr == W_EBREAK) ||
                 (HAS_ZICSR && (instr == W_MRET || instr == W_WFI));
          else
            ok = HAS_ZICSR && (f3 != 3'b100);
        end
        default: ok = 1'b0;
      endcase
    end
  end

  assign illegal = !ok;

  // raw wiring: valid for any word
  assign funct3   = f3;
  assign csr_addr = instr[31:20];
  assign zimm     = instr[19:15];

  logic sys_csr;  // legal CSR* word
  assign sys_csr = ok && (opc == OPC_SYSTEM) && (f3 != 3'b000);

  assign is_branch = ok && (opc == OPC_BRANCH);
  assign is_jal    = ok && (opc == OPC_JAL);
  assign is_jalr   = ok && (opc == OPC_JALR);
  assign is_load   = ok && (opc == OPC_LOAD);
  assign is_store  = ok && (opc == OPC_STORE);
  assign is_mul    = ok && md && !f3[2];
  assign is_div    = ok && md &&  f3[2];
  assign is_csr    = sys_csr;
  assign csr_imm   = sys_csr && f3[2];
  assign csr_wr    = sys_csr && ((f3[1:0] == 2'b01) || (instr[19:15] != 5'b0));
  assign is_ecall  = ok && (instr == W_ECALL);
  assign is_ebreak = ok && (instr == W_EBREAK);
  assign is_mret   = ok && (instr == W_MRET);
  assign is_wfi    = ok && (instr == W_WFI);
  assign is_fence  = ok && (opc == OPC_FENCE) && (f3 == 3'b000);
  assign is_fencei = ok && (opc == OPC_FENCE) && (f3 == 3'b001);

  // operand usage
  logic uses_rs1, uses_rs2, uses_rd;
  always_comb begin
    uses_rs1 = ok && (opc == OPC_OP || opc == OPC_OPIMM || opc == OPC_LOAD || opc == OPC_STORE ||
                      opc == OPC_BRANCH || opc == OPC_JALR || (sys_csr && !f3[2]));
    uses_rs2 = ok && (opc == OPC_OP || opc == OPC_STORE || opc == OPC_BRANCH);
    uses_rd  = ok && (opc == OPC_OP || opc == OPC_OPIMM || opc == OPC_LOAD || opc == OPC_LUI ||
                      opc == OPC_AUIPC || opc == OPC_JAL || opc == OPC_JALR || sys_csr);
  end
  assign rs1_addr = uses_rs1 ? instr[19:15] : 5'b0;
  assign rs2_addr = uses_rs2 ? instr[24:20] : 5'b0;
  assign rd_addr  = uses_rd  ? instr[11:7]  : 5'b0;

  // immediate
  always_comb begin
    imm = 32'b0;
    if (ok) begin
      case (opc)
        OPC_OPIMM, OPC_LOAD, OPC_JALR: imm = imm_i;
        OPC_STORE:                     imm = imm_s;
        OPC_BRANCH:                    imm = imm_b;
        OPC_LUI, OPC_AUIPC:            imm = imm_u;
        OPC_JAL:                       imm = imm_j;
        default:                       imm = 32'b0;
      endcase
    end
  end

  // datapath selects
  always_comb begin
    alu_op = ALU_ADD;
    if (ok && opc == OPC_OP && !md)
      alu_op = {instr[30], f3};
    else if (ok && opc == OPC_OPIMM)
      alu_op = {instr[30] && (f3 == 3'b101), f3};
  end

  assign alu_a_sel = (ok && opc == OPC_AUIPC) ? ASEL_PC : (ok && opc == OPC_LUI) ? ASEL_ZERO : ASEL_RS1;
  assign alu_b_imm = ok && (opc == OPC_OPIMM || opc == OPC_LOAD || opc == OPC_STORE ||
                            opc == OPC_JALR || opc == OPC_LUI || opc == OPC_AUIPC);

  always_comb begin
    res_sel = RES_ALU;
    if (ok) begin
      if (opc == OPC_JAL || opc == OPC_JALR) res_sel = RES_PC4;
      else if (opc == OPC_LOAD)              res_sel = RES_LOAD;
      else if (sys_csr)                      res_sel = RES_CSR;
      else if (md)                           res_sel = RES_MD;
    end
  end

  assign mem_size     = (is_load || is_store) ? f3[1:0] : MEM_B;
  assign mem_unsigned = is_load && f3[2];
endmodule
