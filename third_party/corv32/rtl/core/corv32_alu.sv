// corv32_alu - combinational integer ALU and branch comparator (DESIGN 6.1).
module corv32_alu (
  input  logic [31:0] a, b,
  input  logic [3:0]  op,        // {alt, funct3}
  input  logic [2:0]  br_funct3, // branch condition (instr funct3)
  output logic [31:0] result,
  output logic        br_taken   // condition(a, b) per br_funct3
);
  `include "corv32_defs.svh"

  logic signed [31:0] sa;
  logic [4:0]  sh;
  logic        lt_s, lt_u;

  assign sa   = a;
  assign sh   = b[4:0];
  assign lt_s = $signed(a) < $signed(b);
  assign lt_u = a < b;

  always_comb begin
    case (op)
      ALU_ADD:  result = a + b;
      ALU_SUB:  result = a - b;
      ALU_SLL:  result = a << sh;
      ALU_SLT:  result = {31'b0, lt_s};
      ALU_SLTU: result = {31'b0, lt_u};
      ALU_XOR:  result = a ^ b;
      ALU_SRL:  result = a >> sh;
      ALU_SRA:  result = 32'(sa >>> sh);
      ALU_OR:   result = a | b;
      ALU_AND:  result = a & b;
      default:  result = 32'b0;
    endcase
  end

  always_comb begin
    case (br_funct3)
      3'b000:  br_taken = (a == b);
      3'b001:  br_taken = (a != b);
      3'b100:  br_taken = lt_s;
      3'b101:  br_taken = !lt_s;
      3'b110:  br_taken = lt_u;
      3'b111:  br_taken = !lt_u;
      default: br_taken = 1'b0;
    endcase
  end
endmodule
