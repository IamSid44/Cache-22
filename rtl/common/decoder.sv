// Instruction decoder: one RV64I instruction word in, control bundle and
// sign-extended immediate out. Purely combinational.

module decoder
    import rv_pkg::*;
(
    input  logic [31:0] instr,
    output ctrl_t       ctrl,
    output logic [63:0] imm
);
    wire [6:0] opcode = instr[6:0];
    wire [2:0] funct3 = instr[14:12];
    wire [6:0] funct7 = instr[31:25];
    wire [5:0] top6   = instr[31:26];

    wire [63:0] imm_i = {{52{instr[31]}}, instr[31:20]};
    wire [63:0] imm_s = {{52{instr[31]}}, instr[31:25], instr[11:7]};
    wire [63:0] imm_b = {{51{instr[31]}}, instr[31], instr[7], instr[30:25], instr[11:8], 1'b0};
    wire [63:0] imm_u = {{32{instr[31]}}, instr[31:12], 12'b0};
    wire [63:0] imm_j = {{43{instr[31]}}, instr[31], instr[19:12], instr[20], instr[30:21], 1'b0};

    wire f7_zero = (funct7 == 7'h00);
    wire f7_alt  = (funct7 == 7'h20);       // sub / sra
    wire f3_addsub = (funct3 == 3'b000);
    wire f3_sll    = (funct3 == 3'b001);
    wire f3_srx    = (funct3 == 3'b101);
    wire alt_bit   = instr[30];
    wire f3_bit2   = funct3[2];

    wire [4:0]  rs1_field = instr[19:15];
    wire [11:0] csr_addr  = instr[31:20];
    wire csr_writes    = (funct3[1:0] == 2'b01) || (rs1_field != 5'd0);
    wire csr_read_only = (csr_addr[11:10] == 2'b11);
    wire csr_known =
        csr_addr == CSR_MSTATUS  || csr_addr == CSR_MISA      || csr_addr == CSR_MIE     ||
        csr_addr == CSR_MTVEC    || csr_addr == CSR_MSCRATCH  || csr_addr == CSR_MEPC    ||
        csr_addr == CSR_MCAUSE   || csr_addr == CSR_MTVAL     || csr_addr == CSR_MIP     ||
        csr_addr == CSR_MCYCLE   || csr_addr == CSR_MINSTRET  || csr_addr == CSR_MVENDORID ||
        csr_addr == CSR_MARCHID  || csr_addr == CSR_MIMPID    || csr_addr == CSR_MHARTID;

    // ALU operation selected by funct3 for OP / OP-IMM style instructions.
    alu_op_e op_from_f3;
    always_comb begin
        case (funct3)
            3'b000:  op_from_f3 = ALU_ADD;
            3'b001:  op_from_f3 = ALU_SLL;
            3'b010:  op_from_f3 = ALU_SLT;
            3'b011:  op_from_f3 = ALU_SLTU;
            3'b100:  op_from_f3 = ALU_XOR;
            3'b101:  if (alt_bit) op_from_f3 = ALU_SRA; else op_from_f3 = ALU_SRL;
            3'b110:  op_from_f3 = ALU_OR;
            default: op_from_f3 = ALU_AND;
        endcase
    end

    logic bad;                              // instruction is illegal
    always_comb begin
        bad         = 1'b0;
        ctrl        = '0;
        ctrl.funct3 = funct3;
        ctrl.alu_op = ALU_ADD;
        ctrl.alu_a  = A_RS1;
        ctrl.alu_b  = B_IMM;
        imm         = imm_i;

        case (opcode)
            OPC_LUI: begin
                ctrl.reg_write = 1'b1;
                ctrl.alu_a     = A_ZERO;
                imm            = imm_u;
            end
            OPC_AUIPC: begin
                ctrl.reg_write = 1'b1;
                ctrl.alu_a     = A_PC;
                imm            = imm_u;
            end
            OPC_JAL: begin                      // rd = pc + 4, target = pc + imm
                ctrl.reg_write = 1'b1;
                ctrl.jal       = 1'b1;
                ctrl.alu_a     = A_PC;
                ctrl.alu_b     = B_FOUR;
                imm            = imm_j;
            end
            OPC_JALR: begin                     // rd = pc + 4, target = rs1 + imm
                bad            = (funct3 != 3'b000);
                ctrl.reg_write = 1'b1;
                ctrl.jalr      = 1'b1;
                ctrl.uses_rs1  = 1'b1;
                ctrl.alu_a     = A_PC;
                ctrl.alu_b     = B_FOUR;
            end
            OPC_BRANCH: begin
                bad           = (funct3 == 3'b010) || (funct3 == 3'b011);
                ctrl.branch   = 1'b1;
                ctrl.uses_rs1 = 1'b1;
                ctrl.uses_rs2 = 1'b1;
                imm           = imm_b;
            end
            OPC_LOAD: begin                     // address = rs1 + imm
                bad            = (funct3 == 3'b111);
                ctrl.load      = 1'b1;
                ctrl.reg_write = 1'b1;
                ctrl.uses_rs1  = 1'b1;
            end
            OPC_STORE: begin
                bad           = f3_bit2;
                ctrl.store    = 1'b1;
                ctrl.uses_rs1 = 1'b1;
                ctrl.uses_rs2 = 1'b1;
                imm           = imm_s;
            end
            OPC_OPIMM: begin
                // Shifts carry a 6-bit amount; the bits above it select
                // logical/arithmetic and must otherwise be zero.
                bad            = (f3_sll && top6 != 6'b000000) ||
                                 (f3_srx && top6 != 6'b000000 && top6 != 6'b010000);
                ctrl.reg_write = 1'b1;
                ctrl.uses_rs1  = 1'b1;
                ctrl.alu_op    = op_from_f3;
            end
            OPC_OPIMM32: begin                  // addiw, slliw, srliw, sraiw
                bad            = !(f3_addsub ||
                                   (f3_sll && f7_zero) ||
                                   (f3_srx && (f7_zero || f7_alt)));
                ctrl.reg_write = 1'b1;
                ctrl.uses_rs1  = 1'b1;
                ctrl.alu_op    = op_from_f3;
                ctrl.alu_word  = 1'b1;
            end
            OPC_OP: begin
                bad            = !(f7_zero || (f7_alt && (f3_addsub || f3_srx)));
                ctrl.reg_write = 1'b1;
                ctrl.uses_rs1  = 1'b1;
                ctrl.uses_rs2  = 1'b1;
                ctrl.alu_b     = B_RS2;
                ctrl.alu_op    = op_from_f3;
                if (f3_addsub && alt_bit) ctrl.alu_op = ALU_SUB;
            end
            OPC_OP32: begin                     // addw, subw, sllw, srlw, sraw
                bad            = !((f3_addsub || f3_sll || f3_srx) &&
                                   (f7_zero || (f7_alt && (f3_addsub || f3_srx))));
                ctrl.reg_write = 1'b1;
                ctrl.uses_rs1  = 1'b1;
                ctrl.uses_rs2  = 1'b1;
                ctrl.alu_b     = B_RS2;
                ctrl.alu_op    = op_from_f3;
                if (f3_addsub && alt_bit) ctrl.alu_op = ALU_SUB;
                ctrl.alu_word  = 1'b1;
            end
            OPC_MISCMEM: begin
                // fence: nothing to do on a single core. Executes as a no-op.
            end
            OPC_SYSTEM: begin
                if (funct3 == 3'b000) begin
                    if (instr == 32'h0000_0073)      ctrl.ecall  = 1'b1;
                    else if (instr == 32'h0010_0073) ctrl.ebreak = 1'b1;
                    else if (instr == 32'h3020_0073) ctrl.mret   = 1'b1;
                    else if (instr != 32'h1050_0073) bad = 1'b1;    // wfi is a no-op
                end else if (funct3 == 3'b100) begin
                    bad = 1'b1;
                end else begin
                    // CSR instruction.
                    bad            = !csr_known || (csr_writes && csr_read_only);
                    ctrl.csr       = 1'b1;
                    ctrl.reg_write = 1'b1;
                    if (f3_bit2) begin
                        ctrl.alu_a = A_ZERO;
                        imm        = {59'b0, rs1_field};
                    end else begin
                        ctrl.uses_rs1 = 1'b1;
                        imm           = 64'b0;
                    end
                end
            end
            default: begin
                bad = 1'b1;
            end
        endcase

        // An illegal instruction must have no effect on the datapath.
        if (bad) begin
            ctrl        = '0;
            ctrl.funct3 = funct3;
        end
        ctrl.illegal = bad;
    end
endmodule
