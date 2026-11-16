// Machine-mode control and status registers.

module csr_file
    import rv_pkg::*;
(
    input  logic        clk,
    input  logic        rst,

    // CSR instruction
    input  logic        csr_en,         // a CSR instruction completes this cycle
    input  logic [11:0] csr_addr,
    input  logic [1:0]  csr_op,         // 01 write, 10 set bits, 11 clear bits
    input  logic        csr_write,      // the instruction writes the register
    input  logic [63:0] csr_src,        // rs1 value or zero-extended immediate
    output logic [63:0] csr_rdata,      // value before the write

    // Trap entry and return
    input  logic        trap,
    input  logic [63:0] trap_pc,
    input  logic [3:0]  trap_cause,
    input  logic [63:0] trap_tval,
    input  logic        mret,

    input  logic        retire,         // an instruction completes without trapping
    input  logic        retire2,        // ... and a second one in the same cycle

    output logic [63:0] mtvec,
    output logic [63:0] mepc
);
    logic        mstatus_mie, mstatus_mpie;
    logic [63:0] mie_q, mip_q, mscratch_q, mcause_q, mtval_q;
    logic [63:0] mtvec_q, mepc_q;
    logic [63:0] minstret_q, mcycle_q;

    assign mtvec = mtvec_q;
    assign mepc  = mepc_q;

    wire [63:0] mstatus_value = {51'b0, 2'b11, 3'b0, mstatus_mpie, 3'b0, mstatus_mie, 3'b0};

    always_comb begin
        case (csr_addr)
            CSR_MSTATUS:  csr_rdata = mstatus_value;
            CSR_MISA:     csr_rdata = MISA_VALUE;
            CSR_MIE:      csr_rdata = mie_q;
            CSR_MTVEC:    csr_rdata = mtvec_q;
            CSR_MSCRATCH: csr_rdata = mscratch_q;
            CSR_MEPC:     csr_rdata = mepc_q;
            CSR_MCAUSE:   csr_rdata = mcause_q;
            CSR_MTVAL:    csr_rdata = mtval_q;
            CSR_MIP:      csr_rdata = mip_q;
            CSR_MCYCLE:   csr_rdata = mcycle_q;
            CSR_MINSTRET: csr_rdata = minstret_q;
            default:      csr_rdata = 64'b0;        // the ID registers
        endcase
    end

    wire [63:0] wval = (csr_op == 2'b01) ? csr_src :
                       (csr_op == 2'b10) ? (csr_rdata | csr_src) :
                                           (csr_rdata & ~csr_src);
    wire        wval_mie     = wval[3];
    wire        wval_mpie    = wval[7];
    wire [63:0] wval_aligned = {wval[63:2], 2'b00};

    always_ff @(posedge clk) begin
        if (rst) begin
            mstatus_mie  <= 1'b0;
            mstatus_mpie <= 1'b0;
            mie_q        <= 64'b0;
            mip_q        <= 64'b0;
            mscratch_q   <= 64'b0;
            mcause_q     <= 64'b0;
            mtval_q      <= 64'b0;
            mtvec_q      <= 64'b0;
            mepc_q       <= 64'b0;
            minstret_q   <= 64'b0;
            mcycle_q     <= 64'b0;
        end else begin
            mcycle_q <= mcycle_q + 64'd1;
            minstret_q <= minstret_q + {63'b0, retire} + {63'b0, retire2};

            if (trap) begin
                // Save where and why, and stack the interrupt enable.
                mepc_q       <= trap_pc;
                mcause_q     <= {60'b0, trap_cause};
                mtval_q      <= trap_tval;
                mstatus_mpie <= mstatus_mie;
                mstatus_mie  <= 1'b0;
            end else if (mret) begin
                mstatus_mie  <= mstatus_mpie;
                mstatus_mpie <= 1'b1;
            end else if (csr_en && csr_write) begin
                case (csr_addr)
                    CSR_MSTATUS: begin
                        mstatus_mie  <= wval_mie;
                        mstatus_mpie <= wval_mpie;
                    end
                    CSR_MIE:      mie_q      <= wval;
                    CSR_MTVEC:    mtvec_q    <= wval_aligned;
                    CSR_MSCRATCH: mscratch_q <= wval;
                    CSR_MEPC:     mepc_q     <= wval_aligned;
                    CSR_MCAUSE:   mcause_q   <= wval;
                    CSR_MTVAL:    mtval_q    <= wval;
                    CSR_MIP:      mip_q      <= wval;
                    // The writing instruction itself then retires, so the next
                    // instruction reads the written value plus one.
                    CSR_MINSTRET: minstret_q <= wval + 64'd1;
                    default: ;                  // misa, mcycle: writes ignored
                endcase
            end
        end
    end
endmodule
