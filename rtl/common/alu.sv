// Arithmetic/logic unit for RV64I. Combinational.

module alu
    import rv_pkg::*;
(
    input  logic [63:0] a,
    input  logic [63:0] b,
    input  alu_op_e     op,
    input  logic        word,
    output logic [63:0] y
);
    wire [31:0] a_lo = a[31:0];
    wire [4:0]  b_lo5 = b[4:0];
    wire [5:0]  b_lo6 = b[5:0];

    // Right shifts of a 32-bit value: bring in zeros (srlw) or copies of bit 31
    // (sraw) by extending the operand first.
    wire [63:0] shift_in  = !word            ? a :
                            (op == ALU_SRA)  ? {{32{a_lo[31]}}, a_lo} :
                                               {32'b0, a_lo};
    wire [5:0]  shamt     = word ? {1'b0, b_lo5} : b_lo6;

    logic [63:0] full;
    always_comb begin
        case (op)
            ALU_ADD:  full = a + b;
            ALU_SUB:  full = a - b;
            ALU_SLL:  full = a << shamt;
            ALU_SLT:  full = {63'b0, $signed(a) < $signed(b)};
            ALU_SLTU: full = {63'b0, a < b};
            ALU_XOR:  full = a ^ b;
            ALU_SRL:  full = shift_in >> shamt;
            ALU_SRA:  full = $signed(shift_in) >>> shamt;
            ALU_OR:   full = a | b;
            ALU_AND:  full = a & b;
            default:  full = 64'b0;
        endcase
    end

    wire [31:0] full_lo = full[31:0];
    assign y = word ? {{32{full_lo[31]}}, full_lo} : full;
endmodule
