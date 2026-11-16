// In-order 5-stage RV64I pipeline: IF, ID, EX, MEM, WB.

module inorder_core
    import rv_pkg::*;
#(
    parameter logic [63:0] RESET_PC = 64'h0,
    // Branch predictor (rtl/common/bpred.sv). BP_MODE 0 = always not taken.
    parameter int BP_MODE         = 0,
    parameter int BP_ENTRIES      = 64,
    parameter int BP_TAG_BITS     = 0,
    parameter int BP_COUNTER_BITS = 2
) (
    input  logic        clk,
    input  logic        rst,            // synchronous, active high

    // instruction memory
    output logic        imem_req,       // the core will take the instruction if ready
    output logic [63:0] imem_addr,
    input  logic [31:0] imem_rdata,
    input  logic        imem_ready,     // imem_rdata / imem_fault are valid
    input  logic        imem_fault,     // nothing at imem_addr

    // data memory
    output logic        dmem_re,
    output logic        dmem_we,
    output logic [63:0] dmem_addr,
    output logic [63:0] dmem_pc,        // pc of the load or store (for the prefetcher)
    output logic [1:0]  dmem_size,      // 0: byte, 1: half, 2: word, 3: doubleword
    output logic [63:0] dmem_wdata,
    input  logic [63:0] dmem_rdata,
    input  logic        dmem_ready,     // the access completes this cycle
    input  logic        dmem_fault,     // nothing at dmem_addr

    // commit report
    output logic        commit_valid,
    output logic [63:0] commit_pc,
    output logic [31:0] commit_instr,
    output logic        commit_trap,    // the instruction trapped instead of retiring
    output logic [3:0]  commit_cause,
    output logic [63:0] commit_tval,
    output logic        commit_rd_we,
    output logic [4:0]  commit_rd,
    output logic [63:0] commit_rd_data,
    output logic        commit_mem_we,
    output logic [63:0] commit_mem_addr,
    output logic [1:0]  commit_mem_size,
    output logic [63:0] commit_mem_data,

    // event counters for performance reporting
    output logic [63:0] cnt_redirect,   // mispredictions: fetch redirected from EX (2 cycles each)
    output logic [63:0] cnt_loaduse,    // load-use stall cycles
    output logic [63:0] cnt_flush,      // traps and mrets (each costs 3 cycles)
    output logic [63:0] cnt_ifwait,     // cycles fetch waited for memory
    output logic [63:0] cnt_hold,       // cycles the pipeline was held for a data access
    output logic [63:0] cnt_branch,     // conditional branches executed
    output logic [63:0] cnt_branch_mis, // ... of which mispredicted
    output logic [63:0] cnt_jump,       // jal and jalr executed
    output logic [63:0] cnt_jump_mis    // ... of which mispredicted
);

    // ------------------------------------------------------------------
    // Signals that cross stage boundaries backwards
    // ------------------------------------------------------------------
    logic        stall;                 // hold IF and ID, insert a bubble in EX
    logic        redirect;              // EX changes the pc (branch or jump)
    logic [63:0] redirect_pc;
    logic        flush;                 // MEM changes the pc (trap or mret)
    logic [63:0] flush_pc;
    logic        hold;                  // data access not finished: freeze everything

    // ------------------------------------------------------------------
    // IF
    // ------------------------------------------------------------------
    logic [63:0] pc_q;

    assign imem_addr = pc_q;
    assign imem_req  = !stall && !hold;

    // The instruction at pc is not available yet (instruction cache miss).
    wire if_wait = !imem_ready;

    // Prediction for the instruction at pc, made from the pc alone.
    logic        if_pred_taken;
    logic [63:0] if_pred_target;
    logic        bp_train;              // an instruction resolves in EX this cycle
    logic        bp_taken;
    logic [63:0] bp_target;
    logic [63:0] bp_pc;

    bpred #(
        .MODE         (BP_MODE),
        .ENTRIES      (BP_ENTRIES),
        .TAG_BITS     (BP_TAG_BITS),
        .COUNTER_BITS (BP_COUNTER_BITS)
    ) u_bpred (
        .clk         (clk),
        .rst         (rst),
        .pc          (pc_q),
        .pred_taken  (if_pred_taken),
        .pred_target (if_pred_target),
        .upd_valid   (bp_train),
        .upd_pc      (bp_pc),
        .upd_taken   (bp_taken),
        .upd_target  (bp_target),
        // one instruction per cycle: the second ports are not used
        .pc2          (64'b0),
        .pred2_taken  (),
        .pred2_target (),
        .upd2_valid   (1'b0),
        .upd2_pc      (64'b0),
        .upd2_taken   (1'b0),
        .upd2_target  (64'b0)
    );

    wire [63:0] if_pred_next = if_pred_taken ? if_pred_target : pc_q + 64'd4;

    // A flush comes from an older instruction than a redirect, so it wins.
    always_ff @(posedge clk) begin
        if (rst) begin
            pc_q <= RESET_PC;
        end else if (!hold) begin
            if (flush)                    pc_q <= flush_pc;
            else if (redirect)            pc_q <= redirect_pc;
            else if (!stall && !if_wait)  pc_q <= if_pred_next;
        end
    end

    // IF/ID register
    logic        id_valid;
    logic [63:0] id_pc;
    logic [31:0] id_instr;
    logic        id_fetch_fault;
    logic [63:0] id_pred_next;          // where fetch went after this instruction

    always_ff @(posedge clk) begin
        if (rst) begin
            id_valid <= 1'b0;
        end else if (!hold) begin
            if (flush || redirect) begin
                id_valid <= 1'b0;
            end else if (!stall) begin
                id_valid       <= !if_wait;     // a bubble while fetch waits
                id_pc          <= pc_q;
                id_instr       <= imem_rdata;
                id_fetch_fault <= imem_fault;
                id_pred_next   <= if_pred_next;
            end
        end
    end

    // ------------------------------------------------------------------
    // ID
    // ------------------------------------------------------------------
    ctrl_t       id_ctrl;
    logic [63:0] id_imm;
    logic [63:0] id_rs1_val, id_rs2_val;

    wire [4:0] id_rs1 = id_instr[19:15];
    wire [4:0] id_rs2 = id_instr[24:20];
    wire [4:0] id_rd  = id_instr[11:7];

    decoder u_decoder (
        .instr (id_instr),
        .ctrl  (id_ctrl),
        .imm   (id_imm)
    );

    // Exceptions known at decode.
    wire        id_exc   = id_fetch_fault || id_ctrl.illegal || id_ctrl.ecall || id_ctrl.ebreak;
    wire [3:0]  id_cause = id_fetch_fault  ? CAUSE_INSTR_ACCESS :
                           id_ctrl.illegal ? CAUSE_ILLEGAL :
                           id_ctrl.ecall   ? CAUSE_ECALL_M : CAUSE_BREAKPOINT;
    wire [63:0] id_tval  = id_fetch_fault  ? id_pc :
                           id_ctrl.illegal ? {32'b0, id_instr} :
                           id_ctrl.ecall   ? 64'b0 : id_pc;

    // Writeback signals, declared here because the register file needs them.
    logic        wb_valid;
    ctrl_t       wb_ctrl;
    logic [4:0]  wb_rd;
    logic [63:0] wb_data;

    regfile u_regfile (
        .clk    (clk),
        .we     (wb_valid && wb_ctrl.reg_write),
        .waddr  (wb_rd),
        .wdata  (wb_data),
        .raddr1 (id_rs1),
        .rdata1 (id_rs1_val),
        .raddr2 (id_rs2),
        .rdata2 (id_rs2_val)
    );

    // ID/EX register
    logic        ex_valid;
    logic [63:0] ex_pc;
    logic [31:0] ex_instr;
    ctrl_t       ex_ctrl;
    logic [63:0] ex_imm;
    logic [63:0] ex_rs1_val, ex_rs2_val;
    logic [4:0]  ex_rs1, ex_rs2, ex_rd;
    logic        ex_exc;
    logic [3:0]  ex_cause;
    logic [63:0] ex_tval;
    logic [63:0] ex_pred_next;

    // Load-use hazard: the instruction in EX produces its result only in MEM (a
    // load or a CSR read) and the instruction in ID needs it.
    wire ex_late_result = ex_ctrl.load || ex_ctrl.csr;
    assign stall = id_valid && ex_valid && ex_late_result && (ex_rd != 5'd0) &&
                   ((id_ctrl.uses_rs1 && id_rs1 == ex_rd) ||
                    (id_ctrl.uses_rs2 && id_rs2 == ex_rd));

    always_ff @(posedge clk) begin
        if (rst || (!hold && (flush || redirect || stall))) begin
            ex_valid <= 1'b0;
            ex_ctrl  <= '0;
            ex_exc   <= 1'b0;
        end else if (!hold) begin
            ex_valid <= id_valid;
            // An instruction with an exception must have no other effect.
            if (id_valid && !id_exc) ex_ctrl <= id_ctrl;
            else                     ex_ctrl <= '0;
            ex_exc     <= id_valid && id_exc;
            ex_cause   <= id_cause;
            ex_tval    <= id_tval;
            ex_pc      <= id_pc;
            ex_pred_next <= id_pred_next;
            ex_instr   <= id_instr;
            ex_imm     <= id_imm;
            ex_rs1_val <= id_rs1_val;
            ex_rs2_val <= id_rs2_val;
            ex_rs1     <= id_rs1;
            ex_rs2     <= id_rs2;
            ex_rd      <= id_rd;
        end
    end

    // ------------------------------------------------------------------
    // EX
    // ------------------------------------------------------------------
    // EX/MEM register (declared early: forwarding reads it)
    logic        mem_valid;
    logic [63:0] mem_pc;
    logic [31:0] mem_instr;
    ctrl_t       mem_ctrl;
    logic [4:0]  mem_rd;
    logic [63:0] mem_alu;               // ALU result: load/store address, CSR source
    logic [63:0] mem_store_data;
    logic        mem_exc;
    logic [3:0]  mem_cause;
    logic [63:0] mem_tval;

    // Forwarding.
    wire fwd_mem_rs1 = mem_ctrl.reg_write && (mem_rd != 5'd0) && (mem_rd == ex_rs1);
    wire fwd_mem_rs2 = mem_ctrl.reg_write && (mem_rd != 5'd0) && (mem_rd == ex_rs2);
    wire fwd_wb_rs1  = wb_ctrl.reg_write  && (wb_rd  != 5'd0) && (wb_rd  == ex_rs1);
    wire fwd_wb_rs2  = wb_ctrl.reg_write  && (wb_rd  != 5'd0) && (wb_rd  == ex_rs2);

    wire [63:0] ex_rs1_fwd = fwd_mem_rs1 ? mem_alu : fwd_wb_rs1 ? wb_data : ex_rs1_val;
    wire [63:0] ex_rs2_fwd = fwd_mem_rs2 ? mem_alu : fwd_wb_rs2 ? wb_data : ex_rs2_val;

    wire [63:0] alu_a = (ex_ctrl.alu_a == A_PC)   ? ex_pc :
                        (ex_ctrl.alu_a == A_ZERO) ? 64'b0 : ex_rs1_fwd;
    wire [63:0] alu_b = (ex_ctrl.alu_b == B_IMM)  ? ex_imm :
                        (ex_ctrl.alu_b == B_FOUR) ? 64'd4 : ex_rs2_fwd;
    logic [63:0] ex_alu;

    alu u_alu (
        .a    (alu_a),
        .b    (alu_b),
        .op   (ex_ctrl.alu_op),
        .word (ex_ctrl.alu_word),
        .y    (ex_alu)
    );

    // Branch condition
    wire br_eq  = (ex_rs1_fwd == ex_rs2_fwd);
    wire br_lt  = ($signed(ex_rs1_fwd) < $signed(ex_rs2_fwd));
    wire br_ltu = (ex_rs1_fwd < ex_rs2_fwd);
    wire [2:0] ex_funct3 = ex_ctrl.funct3;
    logic br_cond;
    always_comb begin
        case (ex_funct3)
            3'b000:  br_cond = br_eq;
            3'b001:  br_cond = !br_eq;
            3'b100:  br_cond = br_lt;
            3'b101:  br_cond = !br_lt;
            3'b110:  br_cond = br_ltu;
            3'b111:  br_cond = !br_ltu;
            default: br_cond = 1'b0;
        endcase
    end

    // Target: pc + imm for branches and jal, (rs1 + imm) with bit 0 cleared for
    // jalr.
    wire [63:0] target_base = ex_ctrl.jalr ? ex_rs1_fwd : ex_pc;
    wire [63:0] target_sum  = target_base + ex_imm;
    wire [63:0] ex_target   = {target_sum[63:1], target_sum[0] & ~ex_ctrl.jalr};

    wire ex_taken             = ex_ctrl.jal || ex_ctrl.jalr || (ex_ctrl.branch && br_cond);
    wire ex_target_misaligned = ex_taken && ex_target[1];
    wire ex_transfers         = ex_taken && !ex_target_misaligned;

    // The real next pc of the instruction in EX, whatever kind it is.
    wire [63:0] ex_next_pc = ex_transfers ? ex_target : ex_pc + 64'd4;

    assign redirect    = ex_valid && (ex_next_pc != ex_pred_next);
    assign redirect_pc = ex_next_pc;

    // Train the predictor once per instruction, with what really happened.
    assign bp_train  = ex_valid && !hold && !flush;
    assign bp_pc     = ex_pc;
    assign bp_taken  = ex_transfers;
    assign bp_target = ex_target;

    always_ff @(posedge clk) begin
        if (rst || (!hold && flush)) begin
            mem_valid <= 1'b0;
            mem_ctrl  <= '0;
            mem_exc   <= 1'b0;
        end else if (!hold) begin
            mem_valid <= ex_valid;
            if (ex_target_misaligned) begin
                mem_ctrl  <= '0;
                mem_exc   <= 1'b1;
                mem_cause <= CAUSE_INSTR_MISALIGNED;
                mem_tval  <= ex_target;
            end else begin
                mem_ctrl  <= ex_ctrl;
                mem_exc   <= ex_exc;
                mem_cause <= ex_cause;
                mem_tval  <= ex_tval;
            end
            mem_pc         <= ex_pc;
            mem_instr      <= ex_instr;
            mem_rd         <= ex_rd;
            mem_alu        <= ex_alu;
            mem_store_data <= ex_rs2_fwd;
        end
    end

    // ------------------------------------------------------------------
    // MEM
    // ------------------------------------------------------------------
    wire [2:0] mem_funct3 = mem_ctrl.funct3;
    wire [1:0] mem_size   = mem_funct3[1:0];

    // Store data limited to the bytes actually written.
    wire [63:0] size_mask = (mem_size == 2'd0) ? 64'h0000_0000_0000_00FF :
                            (mem_size == 2'd1) ? 64'h0000_0000_0000_FFFF :
                            (mem_size == 2'd2) ? 64'h0000_0000_FFFF_FFFF :
                                                 64'hFFFF_FFFF_FFFF_FFFF;
    wire [63:0] mem_wdata = mem_store_data & size_mask;

    // An access must be aligned to its own size.
    wire [2:0] align_mask = (mem_size == 2'd0) ? 3'b000 :
                            (mem_size == 2'd1) ? 3'b001 :
                            (mem_size == 2'd2) ? 3'b011 : 3'b111;
    wire mem_access     = mem_ctrl.load || mem_ctrl.store;
    wire mem_misaligned = mem_access && ((mem_alu[2:0] & align_mask) != 3'b000);

    // A misaligned access is never sent to memory.
    assign dmem_re    = mem_ctrl.load  && !mem_misaligned;
    assign dmem_we    = mem_ctrl.store && !mem_misaligned;
    assign dmem_addr  = mem_alu;
    assign dmem_pc    = mem_pc;
    assign dmem_size  = mem_size;
    assign dmem_wdata = mem_wdata;

    wire mem_access_fault = (dmem_re || dmem_we) && dmem_fault;

    // The data access has not completed (data cache miss).
    assign hold = (dmem_re || dmem_we) && !dmem_ready;

    // Trap decision.
    wire        trap       = mem_valid && (mem_exc || mem_misaligned || mem_access_fault);
    wire [3:0]  trap_cause = mem_exc        ? mem_cause :
                             mem_misaligned ? (mem_ctrl.store ? CAUSE_STORE_MISALIGNED
                                                              : CAUSE_LOAD_MISALIGNED)
                                            : (mem_ctrl.store ? CAUSE_STORE_ACCESS
                                                              : CAUSE_LOAD_ACCESS);
    wire [63:0] trap_tval  = mem_exc ? mem_tval : mem_alu;
    wire        do_mret    = mem_valid && mem_ctrl.mret;

    logic [63:0] csr_rdata;
    logic [63:0] mtvec, mepc;

    // A CSR instruction writes its register unless it is a set/clear with a
    // zero source (rs1 = x0 or immediate 0; both are instruction bits 19:15).
    wire [1:0] mem_csr_op    = mem_funct3[1:0];
    wire       mem_csr_write = (mem_csr_op == 2'b01) || (mem_instr[19:15] != 5'd0);

    csr_file u_csr (
        .clk        (clk),
        .rst        (rst),
        .csr_en     (mem_valid && mem_ctrl.csr),
        .csr_addr   (mem_instr[31:20]),
        .csr_op     (mem_csr_op),
        .csr_write  (mem_csr_write),
        .csr_src    (mem_alu),
        .csr_rdata  (csr_rdata),
        .trap       (trap),
        .trap_pc    (mem_pc),
        .trap_cause (trap_cause),
        .trap_tval  (trap_tval),
        .mret       (do_mret),
        .retire     (mem_valid && !trap && !hold),
        .retire2    (1'b0),
        .mtvec      (mtvec),
        .mepc       (mepc)
    );

    assign flush    = trap || do_mret;
    assign flush_pc = trap ? mtvec : mepc;

    // Sign- or zero-extend the loaded value. funct3[2] set means unsigned.
    wire [7:0]  ld_b = dmem_rdata[7:0];
    wire [15:0] ld_h = dmem_rdata[15:0];
    wire [31:0] ld_w = dmem_rdata[31:0];
    wire        ld_unsigned = mem_funct3[2];
    wire [63:0] load_data =
        (mem_size == 2'd0) ? {{56{ld_b[7]  & ~ld_unsigned}}, ld_b} :
        (mem_size == 2'd1) ? {{48{ld_h[15] & ~ld_unsigned}}, ld_h} :
        (mem_size == 2'd2) ? {{32{ld_w[31] & ~ld_unsigned}}, ld_w} :
                             dmem_rdata;

    // MEM/WB register
    logic [63:0] wb_pc;
    logic [31:0] wb_instr;
    logic [63:0] wb_mem_addr;
    logic [63:0] wb_mem_data;
    logic        wb_trap;
    logic [3:0]  wb_cause;
    logic [63:0] wb_tval;

    always_ff @(posedge clk) begin
        if (rst) begin
            wb_valid <= 1'b0;
            wb_ctrl  <= '0;
            wb_trap  <= 1'b0;
        end else if (!hold) begin
            wb_valid <= mem_valid;
            // A trapping instruction goes on to WB only to be reported; it must
            // not write its register.
            if (trap) wb_ctrl <= '0;
            else      wb_ctrl <= mem_ctrl;
            wb_trap     <= trap;
            wb_cause    <= trap_cause;
            wb_tval     <= trap_tval;
            wb_pc       <= mem_pc;
            wb_instr    <= mem_instr;
            wb_rd       <= mem_rd;
            wb_data     <= mem_ctrl.load ? load_data :
                           mem_ctrl.csr  ? csr_rdata : mem_alu;
            wb_mem_addr <= mem_alu;
            wb_mem_data <= mem_wdata;
        end
    end

    // ------------------------------------------------------------------
    // WB / commit report
    // ------------------------------------------------------------------
    wire [2:0] wb_funct3 = wb_ctrl.funct3;

    // While the pipeline is held, the instruction in WB stays there.
    logic held_q;
    always_ff @(posedge clk) begin
        if (rst) held_q <= 1'b0;
        else     held_q <= hold;
    end

    assign commit_valid    = wb_valid && !held_q;
    assign commit_pc       = wb_pc;
    assign commit_instr    = wb_instr;
    assign commit_trap     = wb_trap;
    assign commit_cause    = wb_cause;
    assign commit_tval     = wb_tval;
    assign commit_rd_we    = wb_ctrl.reg_write && (wb_rd != 5'd0);
    assign commit_rd       = wb_rd;
    assign commit_rd_data  = wb_data;
    assign commit_mem_we   = wb_ctrl.store;
    assign commit_mem_addr = wb_mem_addr;
    assign commit_mem_size = wb_funct3[1:0];
    assign commit_mem_data = wb_mem_data;

    // ------------------------------------------------------------------
    // Counters
    // ------------------------------------------------------------------
    always_ff @(posedge clk) begin
        if (rst) begin
            cnt_redirect <= 64'd0;
            cnt_loaduse  <= 64'd0;
            cnt_flush    <= 64'd0;
            cnt_ifwait   <= 64'd0;
            cnt_hold     <= 64'd0;
            cnt_branch     <= 64'd0;
            cnt_branch_mis <= 64'd0;
            cnt_jump       <= 64'd0;
            cnt_jump_mis   <= 64'd0;
        end else if (hold) begin
            cnt_hold <= cnt_hold + 64'd1;
        end else begin
            if (bp_train && ex_ctrl.branch) begin
                cnt_branch <= cnt_branch + 64'd1;
                if (redirect) cnt_branch_mis <= cnt_branch_mis + 64'd1;
            end
            if (bp_train && (ex_ctrl.jal || ex_ctrl.jalr)) begin
                cnt_jump <= cnt_jump + 64'd1;
                if (redirect) cnt_jump_mis <= cnt_jump_mis + 64'd1;
            end
            if (flush)         cnt_flush    <= cnt_flush + 64'd1;
            else if (redirect) cnt_redirect <= cnt_redirect + 64'd1;
            else if (stall)    cnt_loaduse  <= cnt_loaduse + 64'd1;
            else if (if_wait)  cnt_ifwait   <= cnt_ifwait + 64'd1;
        end
    end
endmodule
