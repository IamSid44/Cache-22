// Shared definitions for every core in this repository.

package rv_pkg;

    // Major opcodes (instr[6:0]).
    localparam logic [6:0] OPC_LOAD    = 7'h03;
    localparam logic [6:0] OPC_MISCMEM = 7'h0F;
    localparam logic [6:0] OPC_OPIMM   = 7'h13;
    localparam logic [6:0] OPC_AUIPC   = 7'h17;
    localparam logic [6:0] OPC_OPIMM32 = 7'h1B;
    localparam logic [6:0] OPC_STORE   = 7'h23;
    localparam logic [6:0] OPC_OP      = 7'h33;
    localparam logic [6:0] OPC_LUI     = 7'h37;
    localparam logic [6:0] OPC_OP32    = 7'h3B;
    localparam logic [6:0] OPC_BRANCH  = 7'h63;
    localparam logic [6:0] OPC_JALR    = 7'h67;
    localparam logic [6:0] OPC_JAL     = 7'h6F;
    localparam logic [6:0] OPC_SYSTEM  = 7'h73;

    localparam logic [63:0] MMIO_CONSOLE = 64'h0000_0000_1000_0000;
    localparam logic [63:0] MMIO_HALT    = 64'h0000_0000_1000_0008;

    // Exception causes (the value written to mcause).
    localparam logic [3:0] CAUSE_INSTR_MISALIGNED = 4'd0;
    localparam logic [3:0] CAUSE_INSTR_ACCESS     = 4'd1;
    localparam logic [3:0] CAUSE_ILLEGAL          = 4'd2;
    localparam logic [3:0] CAUSE_BREAKPOINT       = 4'd3;
    localparam logic [3:0] CAUSE_LOAD_MISALIGNED  = 4'd4;
    localparam logic [3:0] CAUSE_LOAD_ACCESS      = 4'd5;
    localparam logic [3:0] CAUSE_STORE_MISALIGNED = 4'd6;
    localparam logic [3:0] CAUSE_STORE_ACCESS     = 4'd7;
    localparam logic [3:0] CAUSE_ECALL_M          = 4'd11;

    // Control and status registers.
    localparam logic [11:0] CSR_MSTATUS   = 12'h300;
    localparam logic [11:0] CSR_MISA      = 12'h301;
    localparam logic [11:0] CSR_MIE       = 12'h304;
    localparam logic [11:0] CSR_MTVEC     = 12'h305;
    localparam logic [11:0] CSR_MSCRATCH  = 12'h340;
    localparam logic [11:0] CSR_MEPC      = 12'h341;
    localparam logic [11:0] CSR_MCAUSE    = 12'h342;
    localparam logic [11:0] CSR_MTVAL     = 12'h343;
    localparam logic [11:0] CSR_MIP       = 12'h344;
    localparam logic [11:0] CSR_MCYCLE    = 12'hB00;
    localparam logic [11:0] CSR_MINSTRET  = 12'hB02;
    localparam logic [11:0] CSR_MVENDORID = 12'hF11;
    localparam logic [11:0] CSR_MARCHID   = 12'hF12;
    localparam logic [11:0] CSR_MIMPID    = 12'hF13;
    localparam logic [11:0] CSR_MHARTID   = 12'hF14;

    // misa: 64-bit machine (MXL = 2) with the I extension.
    localparam logic [63:0] MISA_VALUE = 64'h8000_0000_0000_0100;

    typedef enum logic [3:0] {
        ALU_ADD, ALU_SUB, ALU_SLL, ALU_SLT, ALU_SLTU,
        ALU_XOR, ALU_SRL, ALU_SRA, ALU_OR, ALU_AND
    } alu_op_e;

    typedef enum logic [1:0] {A_RS1, A_PC, A_ZERO} alu_a_e;     // first ALU operand
    typedef enum logic [1:0] {B_RS2, B_IMM, B_FOUR} alu_b_e;    // second ALU operand

    // Everything the datapath needs to know about one instruction.
    typedef struct packed {
        logic       illegal;     // not a valid instruction for this core
        logic       uses_rs1;    // reads register rs1
        logic       uses_rs2;    // reads register rs2
        logic       reg_write;   // writes register rd
        alu_op_e    alu_op;
        logic       alu_word;    // 32-bit operation, result sign-extended
        alu_a_e     alu_a;
        alu_b_e     alu_b;
        logic       branch;      // conditional branch, condition in funct3
        logic       jal;
        logic       jalr;
        logic       load;        // size and signedness in funct3
        logic       store;       // size in funct3
        logic       csr;         // CSR instruction; operation in funct3[1:0]
        logic       mret;
        logic       ecall;       // raises an exception
        logic       ebreak;      // raises an exception
        logic [2:0] funct3;
    } ctrl_t;

endpackage
