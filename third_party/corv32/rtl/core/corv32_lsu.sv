// Load/store unit datapath (combinational): byte enables, store-lane replication,
// misalignment detect, and load lane select with sign/zero extension. Little-endian lanes.
// size/ld_size: 0 = B, 1 = H, 2 = W. Encoding 3 is reserved and treated as W.
module corv32_lsu (
  /* verilator lint_off UNUSEDSIGNAL */
  input  logic [31:0] addr,          // effective byte address (EX); only [1:0] matter here
  /* verilator lint_on UNUSEDSIGNAL */
  input  logic [1:0]  size,
  input  logic [31:0] store_data,    // rs2 value
  output logic        misaligned,
  output logic [3:0]  be,
  output logic [31:0] wdata_lanes,
  input  logic [1:0]  ld_addr_lo,    // MEM: addr[1:0] of the load
  input  logic [1:0]  ld_size,
  input  logic        ld_unsigned,
  input  logic [31:0] ld_rdata,      // bus word
  output logic [31:0] ld_value
);
  always_comb begin
    case (size)
      2'd0: begin
        misaligned  = 1'b0;
        be          = 4'b0001 << addr[1:0];
        wdata_lanes = {4{store_data[7:0]}};
      end
      2'd1: begin
        misaligned  = addr[0];
        be          = 4'b0011 << {addr[1], 1'b0};
        wdata_lanes = {2{store_data[15:0]}};
      end
      default: begin
        misaligned  = (addr[1:0] != 2'b00);
        be          = 4'b1111;
        wdata_lanes = store_data;
      end
    endcase
  end

  // Load extract. For a misaligned H the lane is picked by addr[1] only (don't-care: it traps).
  logic [7:0]  ld_byte;
  logic [15:0] ld_half;
  always_comb begin
    ld_byte = ld_rdata[{ld_addr_lo, 3'b000} +: 8];
    ld_half = ld_addr_lo[1] ? ld_rdata[31:16] : ld_rdata[15:0];
    case (ld_size)
      2'd0:    ld_value = {{24{ld_byte[7]  & ~ld_unsigned}}, ld_byte};
      2'd1:    ld_value = {{16{ld_half[15] & ~ld_unsigned}}, ld_half};
      default: ld_value = ld_rdata;
    endcase
  end
endmodule
