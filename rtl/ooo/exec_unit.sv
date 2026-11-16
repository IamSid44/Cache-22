// Execution unit of the out-of-order core: an ALU, and the logic that resolves
// branches and jumps. Purely combinational.

module exec_unit
    import rv_pkg::*;
(
    input  ctrl_t       ctrl,
    input  logic [63:0] v1,             // rs1 value
    input  logic [63:0] v2,             // rs2 value
    input  logic [63:0] imm,
    input  logic [63:0] pc,
    input  logic [63:0] pred,           // where fetch went after this instruction

    output logic [63:0] value,
    output logic [63:0] next,
    output logic        transfers,
    output logic        mis,
    output logic        misalign,
    output logic [63:0] target
);
    wire [63:0] alu_a = (ctrl.alu_a == A_PC)   ? pc :
                        (ctrl.alu_a == A_ZERO) ? 64'b0 : v1;
    wire [63:0] alu_b = (ctrl.alu_b == B_IMM)  ? imm :
                        (ctrl.alu_b == B_FOUR) ? 64'd4 : v2;

    alu u_alu (
        .a    (alu_a),
        .b    (alu_b),
        .op   (ctrl.alu_op),
        .word (ctrl.alu_word),
        .y    (value)
    );

    wire br_eq  = (v1 == v2);
    wire br_lt  = ($signed(v1) < $signed(v2));
    wire br_ltu = (v1 < v2);
    wire [2:0] funct3 = ctrl.funct3;
    logic br_cond;
    always_comb begin
        case (funct3)
            3'b000:  br_cond = br_eq;
            3'b001:  br_cond = !br_eq;
            3'b100:  br_cond = br_lt;
            3'b101:  br_cond = !br_lt;
            3'b110:  br_cond = br_ltu;
            3'b111:  br_cond = !br_ltu;
            default: br_cond = 1'b0;
        endcase
    end

    wire [63:0] base  = ctrl.jalr ? v1 : pc;
    wire [63:0] sum   = base + imm;
    wire        taken = ctrl.jal || ctrl.jalr || (ctrl.branch && br_cond);

    assign target    = {sum[63:1], sum[0] & ~ctrl.jalr};
    assign misalign  = taken && target[1];
    assign transfers = taken && !misalign;
    assign next      = transfers ? target : pc + 64'd4;
    assign mis       = !misalign && (next != pred);
endmodule
