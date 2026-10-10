// corv32_defs.svh - shared constants (DESIGN 0, 6.1, 6.2, 7.5; SPEC 4.2). Localparams only.
//
// `include this INSIDE a module body (after the port list). No include guard on purpose:
// localparams are scoped to the module, so a `ifndef guard would leave every module after the
// first without them. Include it at most once per module.
//
// Each module uses only a few of these, so unused-parameter lint is switched off around them.
/* verilator lint_off UNUSEDPARAM */

// ALU op = {alt, funct3}  (DESIGN 6.1)
localparam logic [3:0] ALU_ADD  = 4'b0000;
localparam logic [3:0] ALU_SUB  = 4'b1000;
localparam logic [3:0] ALU_SLL  = 4'b0001;
localparam logic [3:0] ALU_SLT  = 4'b0010;
localparam logic [3:0] ALU_SLTU = 4'b0011;
localparam logic [3:0] ALU_XOR  = 4'b0100;
localparam logic [3:0] ALU_SRL  = 4'b0101;
localparam logic [3:0] ALU_SRA  = 4'b1101;
localparam logic [3:0] ALU_OR   = 4'b0110;
localparam logic [3:0] ALU_AND  = 4'b0111;

// decode.res_sel: which value goes to rd
localparam logic [2:0] RES_ALU  = 3'd0;
localparam logic [2:0] RES_PC4  = 3'd1;  // JAL / JALR link
localparam logic [2:0] RES_LOAD = 3'd2;
localparam logic [2:0] RES_CSR  = 3'd3;
localparam logic [2:0] RES_MD   = 3'd4;  // MUL / DIV

// decode.alu_a_sel
localparam logic [1:0] ASEL_RS1  = 2'd0;
localparam logic [1:0] ASEL_PC   = 2'd1;  // AUIPC
localparam logic [1:0] ASEL_ZERO = 2'd2;  // LUI

// decode.mem_size
localparam logic [1:0] MEM_B = 2'd0;
localparam logic [1:0] MEM_H = 2'd1;
localparam logic [1:0] MEM_W = 2'd2;

// CSR addresses (SPEC 4.2). *_LO / *_HI bound a range of read-as-zero registers.
localparam logic [11:0] CSR_MSTATUS       = 12'h300;
localparam logic [11:0] CSR_MISA          = 12'h301;
localparam logic [11:0] CSR_MIE           = 12'h304;
localparam logic [11:0] CSR_MTVEC         = 12'h305;
localparam logic [11:0] CSR_MSTATUSH      = 12'h310;
localparam logic [11:0] CSR_MCOUNTINHIBIT = 12'h320;
localparam logic [11:0] CSR_MHPMEVENT_LO  = 12'h323;  // mhpmevent3
localparam logic [11:0] CSR_MHPMEVENT_HI  = 12'h33F;  // mhpmevent31
localparam logic [11:0] CSR_MSCRATCH      = 12'h340;
localparam logic [11:0] CSR_MEPC          = 12'h341;
localparam logic [11:0] CSR_MCAUSE        = 12'h342;
localparam logic [11:0] CSR_MTVAL         = 12'h343;
localparam logic [11:0] CSR_MIP           = 12'h344;
localparam logic [11:0] CSR_MCYCLE        = 12'hB00;
localparam logic [11:0] CSR_MINSTRET      = 12'hB02;
localparam logic [11:0] CSR_MHPMCNT_LO    = 12'hB03;  // mhpmcounter3
localparam logic [11:0] CSR_MHPMCNT_HI    = 12'hB1F;  // mhpmcounter31
localparam logic [11:0] CSR_MCYCLEH       = 12'hB80;
localparam logic [11:0] CSR_MINSTRETH     = 12'hB82;
localparam logic [11:0] CSR_MHPMCNTH_LO   = 12'hB83;  // mhpmcounter3h
localparam logic [11:0] CSR_MHPMCNTH_HI   = 12'hB9F;  // mhpmcounter31h
localparam logic [11:0] CSR_CYCLE         = 12'hC00;
localparam logic [11:0] CSR_TIME          = 12'hC01;
localparam logic [11:0] CSR_INSTRET       = 12'hC02;
localparam logic [11:0] CSR_CYCLEH        = 12'hC80;
localparam logic [11:0] CSR_TIMEH         = 12'hC81;
localparam logic [11:0] CSR_INSTRETH      = 12'hC82;
localparam logic [11:0] CSR_MVENDORID     = 12'hF11;
localparam logic [11:0] CSR_MARCHID       = 12'hF12;
localparam logic [11:0] CSR_MIMPID        = 12'hF13;
localparam logic [11:0] CSR_MHARTID       = 12'hF14;
localparam logic [11:0] CSR_MCONFIGPTR    = 12'hF15;

// mcause values (DESIGN 7.5): exceptions, then interrupts (bit 31 set)
localparam logic [31:0] CAUSE_INSN_MISALIGNED  = 32'd0;
localparam logic [31:0] CAUSE_INSN_ACCESS      = 32'd1;
localparam logic [31:0] CAUSE_ILLEGAL          = 32'd2;
localparam logic [31:0] CAUSE_BREAKPOINT       = 32'd3;
localparam logic [31:0] CAUSE_LOAD_MISALIGNED  = 32'd4;
localparam logic [31:0] CAUSE_LOAD_ACCESS      = 32'd5;
localparam logic [31:0] CAUSE_STORE_MISALIGNED = 32'd6;
localparam logic [31:0] CAUSE_STORE_ACCESS     = 32'd7;
localparam logic [31:0] CAUSE_ECALL_M          = 32'd11;
localparam logic [31:0] CAUSE_IRQ_MSI          = 32'h8000_0003;
localparam logic [31:0] CAUSE_IRQ_MTI          = 32'h8000_0007;
localparam logic [31:0] CAUSE_IRQ_MEI          = 32'h8000_000B;

/* verilator lint_on UNUSEDPARAM */
