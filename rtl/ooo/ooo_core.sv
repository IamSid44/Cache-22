// Out-of-order RV64I core: Tomasulo's algorithm with a reorder buffer, one or
// two instructions wide.

module ooo_core
    import rv_pkg::*;
#(
    parameter logic [63:0] RESET_PC = 64'h0,
    // Branch predictor (rtl/common/bpred.sv). BP_MODE 0 = always not taken.
    parameter int BP_MODE         = 0,
    parameter int BP_ENTRIES      = 64,
    parameter int BP_TAG_BITS     = 0,
    parameter int BP_COUNTER_BITS = 2,
    parameter int WIDTH           = 1,      // instructions per cycle: 1 or 2
    parameter int ROB_ENTRIES     = 16,     // power of two, at least 2
    parameter int RS_ENTRIES      = 8,      // at least 1
    parameter int LSQ_ENTRIES     = 8       // power of two, at least 2
) (
    input  logic        clk,
    input  logic        rst,            // synchronous, active high

    // instruction memory
    output logic        imem_req,
    output logic [63:0] imem_addr,
    input  logic [31:0] imem_rdata,
    input  logic        imem_ready,
    input  logic        imem_fault,
    input  logic [31:0] imem_rdata2,    // the instruction at imem_addr + 4 ...
    input  logic        imem_valid2,    // ... if it came with the same access

    // data memory
    output logic        dmem_re,
    output logic        dmem_we,
    output logic [63:0] dmem_addr,
    output logic [63:0] dmem_pc,
    output logic [1:0]  dmem_size,
    output logic [63:0] dmem_wdata,
    input  logic [63:0] dmem_rdata,
    input  logic        dmem_ready,
    input  logic        dmem_fault,

    // commit report: the instruction at the ROB head
    output logic        commit_valid,
    output logic [63:0] commit_pc,
    output logic [31:0] commit_instr,
    output logic        commit_trap,
    output logic [3:0]  commit_cause,
    output logic [63:0] commit_tval,
    output logic        commit_rd_we,
    output logic [4:0]  commit_rd,
    output logic [63:0] commit_rd_data,
    output logic        commit_mem_we,
    output logic [63:0] commit_mem_addr,
    output logic [1:0]  commit_mem_size,
    output logic [63:0] commit_mem_data,

    // second commit report: the instruction behind the head, retiring in the
    // same cycle.
    output logic        commit2_valid,
    output logic [63:0] commit2_pc,
    output logic [31:0] commit2_instr,
    output logic        commit2_rd_we,
    output logic [4:0]  commit2_rd,
    output logic [63:0] commit2_rd_data,

    // event counters
    output logic [63:0] cnt_redirect,   // mispredictions (pipeline discarded at retirement)
    output logic [63:0] cnt_loaduse,    // always 0: there is no load-use stall in this core
    output logic [63:0] cnt_flush,      // traps and mrets
    output logic [63:0] cnt_ifwait,     // cycles fetch waited for memory
    output logic [63:0] cnt_hold,       // cycles a data access waited for memory
    output logic [63:0] cnt_branch,
    output logic [63:0] cnt_branch_mis,
    output logic [63:0] cnt_jump,
    output logic [63:0] cnt_jump_mis
);
    localparam int TW = $clog2(ROB_ENTRIES);    // tag width
    localparam int LW = $clog2(LSQ_ENTRIES);

    // Discard everything in flight and restart fetch (decided at retirement).
    logic        flush;
    logic [63:0] flush_pc;

    // ==================================================================
    // Fetch
    // ==================================================================
    logic [63:0] pc_q;
    wire  [63:0] pc_second = pc_q + 64'd4;
    logic        p0_taken, p1_taken;        // predictions for pc_q and pc_second
    logic [63:0] p0_target, p1_target;
    logic        bp_train, bp_taken, bp2_train, bp2_taken;
    logic [63:0] bp_pc, bp_target, bp2_pc, bp2_target;

    bpred #(
        .MODE         (BP_MODE),
        .ENTRIES      (BP_ENTRIES),
        .TAG_BITS     (BP_TAG_BITS),
        .COUNTER_BITS (BP_COUNTER_BITS)
    ) u_bpred (
        .clk          (clk),
        .rst          (rst),
        .pc           (pc_q),
        .pred_taken   (p0_taken),
        .pred_target  (p0_target),
        .pc2          (pc_second),
        .pred2_taken  (p1_taken),
        .pred2_target (p1_target),
        .upd_valid    (bp_train),
        .upd_pc       (bp_pc),
        .upd_taken    (bp_taken),
        .upd_target   (bp_target),
        .upd2_valid   (bp2_train),
        .upd2_pc      (bp2_pc),
        .upd2_taken   (bp2_taken),
        .upd2_target  (bp2_target)
    );

    // Where fetch goes after each of the two instructions.
    wire [63:0] if_next0 = p0_taken ? p0_target : pc_second;
    wire [63:0] if_next1 = p1_taken ? p1_target : pc_q + 64'd8;
    // The second instruction is fetched with the first if the cache has
    // delivered it and fetch is not predicted to leave after the first.
    wire        if_two   = (WIDTH == 2) && imem_valid2 && !p0_taken;

    // Fetch/decode register: the instructions waiting to be dispatched.
    logic [1:0]  fd_valid;
    logic [63:0] fd_pc    [0:1];
    logic [31:0] fd_instr [0:1];
    logic [1:0]  fd_fault;
    logic [63:0] fd_pred  [0:1];            // where fetch went after it

    logic [1:0] disp;                       // enters the back end this cycle
    wire  fd_left = |(fd_valid & ~disp);    // something stays behind
    wire  if_wait = !imem_ready;

    assign imem_addr = pc_q;
    assign imem_req  = !fd_left;

    always_ff @(posedge clk) begin
        if (rst) begin
            pc_q     <= RESET_PC;
            fd_valid <= 2'b00;
        end else if (flush) begin
            pc_q     <= flush_pc;
            fd_valid <= 2'b00;
        end else if (fd_left) begin
            // Fetch waits.
            if (disp[0]) begin
                fd_valid    <= 2'b01;
                fd_pc[0]    <= fd_pc[1];
                fd_instr[0] <= fd_instr[1];
                fd_fault[0] <= fd_fault[1];
                fd_pred[0]  <= fd_pred[1];
            end
        end else begin
            fd_valid <= {!if_wait && if_two, !if_wait};
            if (!if_wait) begin
                fd_pc[0]    <= pc_q;
                fd_instr[0] <= imem_rdata;
                fd_fault[0] <= imem_fault;
                fd_pred[0]  <= if_next0;
                fd_pc[1]    <= pc_second;
                fd_instr[1] <= imem_rdata2;
                fd_fault[1] <= 1'b0;        // it lies in the same line of RAM
                fd_pred[1]  <= if_next1;
                pc_q        <= if_two ? if_next1 : if_next0;
            end
        end
    end

    // ==================================================================
    // Decode (one decoder per slot)
    // ==================================================================
    ctrl_t       d_ctrl  [0:1];
    logic [63:0] d_imm   [0:1];
    logic [4:0]  d_rs1   [0:1];
    logic [4:0]  d_rs2   [0:1];
    logic [4:0]  d_rd    [0:1];
    logic [3:0]  d_cause [0:1];
    logic [63:0] d_tval  [0:1];
    logic [2:0]  d_f3    [0:1];
    logic [1:0]  d_uses1, d_uses2;          // reads rs1 / rs2
    logic [1:0]  d_exc;                     // an exception known at decode
    logic [1:0]  d_sys, d_mem, d_exec, d_nop;   // where the instruction goes
    logic [1:0]  d_writes;                  // writes a register other than x0
    logic [1:0]  d_store, d_load, d_mret, d_branch, d_jump, d_csr_imm;

    for (genvar s = 0; s < 2; s++) begin : g_decode
        ctrl_t       c;
        logic [63:0] imm;
        wire [31:0]  instr = fd_instr[s];
        wire [63:0]  pc    = fd_pc[s];
        wire         fault = fd_fault[s];

        decoder u_decoder (
            .instr (instr),
            .ctrl  (c),
            .imm   (imm)
        );

        wire exc  = fault || c.illegal || c.ecall || c.ebreak;
        wire sys  = !exc && (c.csr || c.mret);                  // serialised
        wire mem  = !exc && (c.load || c.store);                // load/store queue
        wire xfer = c.branch || c.jal || c.jalr;
        wire exec = !exc && !sys && !mem && (c.reg_write || xfer);  // ALU

        assign d_ctrl[s]    = c;
        assign d_imm[s]     = imm;
        assign d_rs1[s]     = instr[19:15];
        assign d_rs2[s]     = instr[24:20];
        assign d_rd[s]      = instr[11:7];
        assign d_f3[s]      = c.funct3;
        assign d_uses1[s]   = c.uses_rs1;
        assign d_uses2[s]   = c.uses_rs2;
        assign d_exc[s]     = exc;
        assign d_cause[s]   = fault     ? CAUSE_INSTR_ACCESS :
                              c.illegal ? CAUSE_ILLEGAL :
                              c.ecall   ? CAUSE_ECALL_M : CAUSE_BREAKPOINT;
        assign d_tval[s]    = fault     ? pc :
                              c.illegal ? {32'b0, instr} :
                              c.ecall   ? 64'b0 : pc;
        assign d_sys[s]     = sys;
        assign d_mem[s]     = mem;
        assign d_exec[s]    = exec;
        assign d_nop[s]     = !exc && !sys && !mem && !exec;    // fence, wfi
        assign d_writes[s]  = !exc && c.reg_write && (instr[11:7] != 5'd0);
        assign d_store[s]   = mem && c.store;
        assign d_load[s]    = mem && c.load;
        assign d_mret[s]    = sys && c.mret;
        assign d_branch[s]  = !exc && c.branch;
        assign d_jump[s]    = !exc && (c.jal || c.jalr);
        assign d_csr_imm[s] = (c.alu_a == A_ZERO);
    end

    // ==================================================================
    // Architectural register file and register alias table
    // ==================================================================
    // Written at retirement only (below). x0 is never written and stays zero.
    logic [63:0] arf [0:31];
    initial begin
        for (int i = 0; i < 32; i++) arf[i] = 64'b0;
    end

    logic [31:0]   rat_busy;                // value still being computed
    logic [TW-1:0] rat_tag [0:31];          // ... by this ROB entry

    // ==================================================================
    // Reorder buffer
    // ==================================================================
    logic [TW-1:0] rob_head, rob_tail;
    logic [TW:0]   rob_count;
    wire           rob_empty = (rob_count == 0);
    wire           rob_full  = (rob_count == ROB_ENTRIES);
    wire           rob_two   = (rob_count <= ROB_ENTRIES - 2);  // room for two

    logic [ROB_ENTRIES-1:0] rob_done;       // result available / ready to retire
    logic [ROB_ENTRIES-1:0] rob_exc;        // raises an exception
    logic [ROB_ENTRIES-1:0] rob_wr;         // writes its destination register
    logic [ROB_ENTRIES-1:0] rob_store, rob_load;
    logic [ROB_ENTRIES-1:0] rob_sys, rob_mret;
    logic [ROB_ENTRIES-1:0] rob_mis;        // fetch went the wrong way after it
    logic [ROB_ENTRIES-1:0] rob_taken;      // it transferred control
    logic [ROB_ENTRIES-1:0] rob_branch, rob_jump;
    logic [63:0] rob_pc    [0:ROB_ENTRIES-1];
    logic [31:0] rob_instr [0:ROB_ENTRIES-1];
    logic [63:0] rob_value [0:ROB_ENTRIES-1];
    logic [63:0] rob_next  [0:ROB_ENTRIES-1];   // the real next pc
    logic [3:0]  rob_cause [0:ROB_ENTRIES-1];
    logic [63:0] rob_tval  [0:ROB_ENTRIES-1];

    // ==================================================================
    // Common data buses
    // ==================================================================
    // Bus 1 is never used when WIDTH is 1.
    logic [1:0]    cdb_valid;
    logic [TW-1:0] cdb_tag   [0:1];
    logic [63:0]   cdb_value [0:1];
    logic [1:0]    cdb_exc;
    logic [3:0]    cdb_cause [0:1];
    logic [63:0]   cdb_tval  [0:1];

    // ==================================================================
    // Operand lookup at dispatch (per slot)
    // ==================================================================
    // For each source register: ready with a value, or waiting for a tag.
    logic [1:0]    op1_ready, op2_ready;
    logic [63:0]   op1_val [0:1];
    logic [63:0]   op2_val [0:1];
    logic [TW-1:0] op1_tag [0:1];
    logic [TW-1:0] op2_tag [0:1];
    logic [63:0]   d_csr_src [0:1];

    for (genvar s = 0; s < 2; s++) begin : g_operands
        wire [4:0] rs1 = d_rs1[s];
        wire [4:0] rs2 = d_rs2[s];

        // Slot 1 only: the register is written by the instruction in slot 0,
        // which is being dispatched in this same cycle.
        wire s1_pair = (s == 1) && d_writes[0] && d_uses1[s] && (d_rd[0] == rs1);
        wire s2_pair = (s == 1) && d_writes[0] && d_uses2[s] && (d_rd[0] == rs2);

        wire [TW-1:0] s1_tag   = s1_pair ? rob_tail : rat_tag[rs1];
        wire          s1_live  = d_uses1[s] && (rs1 != 5'd0) && rat_busy[rs1];
        wire          s1_inrob = rob_done[s1_tag];
        wire          s1_on0   = cdb_valid[0] && (cdb_tag[0] == s1_tag);
        wire          s1_on1   = cdb_valid[1] && (cdb_tag[1] == s1_tag);
        wire          s1_ready = !s1_pair && (!s1_live || s1_inrob || s1_on0 || s1_on1);
        wire [63:0]   s1_val   = !d_uses1[s] ? 64'b0 :
                                 !s1_live    ? arf[rs1] :
                                 s1_inrob    ? rob_value[s1_tag] :
                                 s1_on0      ? cdb_value[0] : cdb_value[1];

        wire [TW-1:0] s2_tag   = s2_pair ? rob_tail : rat_tag[rs2];
        wire          s2_live  = d_uses2[s] && (rs2 != 5'd0) && rat_busy[rs2];
        wire          s2_inrob = rob_done[s2_tag];
        wire          s2_on0   = cdb_valid[0] && (cdb_tag[0] == s2_tag);
        wire          s2_on1   = cdb_valid[1] && (cdb_tag[1] == s2_tag);
        wire          s2_ready = !s2_pair && (!s2_live || s2_inrob || s2_on0 || s2_on1);
        wire [63:0]   s2_val   = !d_uses2[s] ? 64'b0 :
                                 !s2_live    ? arf[rs2] :
                                 s2_inrob    ? rob_value[s2_tag] :
                                 s2_on0      ? cdb_value[0] : cdb_value[1];

        assign op1_ready[s] = s1_ready;
        assign op1_val[s]   = s1_val;
        assign op1_tag[s]   = s1_tag;
        assign op2_ready[s] = s2_ready;
        assign op2_val[s]   = s2_val;
        assign op2_tag[s]   = s2_tag;

        // A system instruction is dispatched with an empty ROB, so its operand
        // is in the ARF: the register for csrrw/rs/rc, the immediate otherwise.
        assign d_csr_src[s] = d_csr_imm[s] ? d_imm[s] : arf[rs1];
    end

    // ==================================================================
    // Reservation stations
    // ==================================================================
    logic [RS_ENTRIES-1:0] rs_valid, rs_r1, rs_r2;      // in use; operand ready
    ctrl_t         rs_ctrl [0:RS_ENTRIES-1];
    logic [63:0]   rs_v1   [0:RS_ENTRIES-1];
    logic [63:0]   rs_v2   [0:RS_ENTRIES-1];
    logic [TW-1:0] rs_t1   [0:RS_ENTRIES-1];
    logic [TW-1:0] rs_t2   [0:RS_ENTRIES-1];
    logic [63:0]   rs_imm  [0:RS_ENTRIES-1];
    logic [63:0]   rs_pc   [0:RS_ENTRIES-1];
    logic [63:0]   rs_pred [0:RS_ENTRIES-1];
    logic [TW-1:0] rs_tag  [0:RS_ENTRIES-1];

    // Free stations for the instructions being dispatched: the first two.
    logic        rs_any0, rs_any1;          // there is one / a second one
    logic [31:0] rs_free0, rs_free1;
    always_comb begin
        rs_any0  = 1'b0;
        rs_any1  = 1'b0;
        rs_free0 = 32'd0;
        rs_free1 = 32'd0;
        for (int k = 0; k < RS_ENTRIES; k++) begin
            if (!rs_valid[k]) begin
                if (!rs_any0) begin
                    rs_any0  = 1'b1;
                    rs_free0 = k;
                end else if (!rs_any1) begin
                    rs_any1  = 1'b1;
                    rs_free1 = k;
                end
            end
        end
    end

    // Select: the oldest station whose operands are both ready, and the oldest
    // of the others.
    logic [1:0]    sel_any;
    logic [31:0]   sel [0:1];
    logic [31:0]   sel0, sel1;
    logic          any0, any1;
    logic [TW-1:0] sel_age, age_k;
    always_comb begin
        any0    = 1'b0;
        any1    = 1'b0;
        sel0    = 32'd0;
        sel1    = 32'd0;
        sel_age = '0;
        age_k   = '0;
        for (int k = 0; k < RS_ENTRIES; k++) begin
            if (rs_valid[k] && rs_r1[k] && rs_r2[k]) begin
                age_k = rs_tag[k] - rob_head;
                if (!any0 || age_k < sel_age) begin
                    any0    = 1'b1;
                    sel0    = k;
                    sel_age = age_k;
                end
            end
        end
        sel_age = '0;
        for (int k = 0; k < RS_ENTRIES; k++) begin
            if (rs_valid[k] && rs_r1[k] && rs_r2[k] && !(any0 && sel0 == k)) begin
                age_k = rs_tag[k] - rob_head;
                if (!any1 || age_k < sel_age) begin
                    any1    = 1'b1;
                    sel1    = k;
                    sel_age = age_k;
                end
            end
        end
    end
    assign sel_any = {any1, any0};
    assign sel[0]  = sel0;
    assign sel[1]  = sel1;

    // ==================================================================
    // Load/store queue
    // ==================================================================
    logic [LW-1:0] lsq_head, lsq_tail;
    logic [LW:0]   lsq_count;
    wire           lsq_full = (lsq_count == LSQ_ENTRIES);

    logic [LSQ_ENTRIES-1:0] lsq_valid;
    logic [LSQ_ENTRIES-1:0] lsq_store;      // store (else load)
    logic [LSQ_ENTRIES-1:0] lsq_rb, lsq_rd; // base / data operand ready
    logic [LSQ_ENTRIES-1:0] lsq_av;         // address computed
    logic [LSQ_ENTRIES-1:0] lsq_mis;        // address misaligned for its size
    logic [LSQ_ENTRIES-1:0] lsq_done;       // load: value delivered; store: ready to retire
    logic [63:0]   lsq_vb   [0:LSQ_ENTRIES-1];
    logic [63:0]   lsq_vd   [0:LSQ_ENTRIES-1];
    logic [TW-1:0] lsq_tb   [0:LSQ_ENTRIES-1];
    logic [TW-1:0] lsq_td   [0:LSQ_ENTRIES-1];
    logic [63:0]   lsq_imm  [0:LSQ_ENTRIES-1];
    logic [63:0]   lsq_addr [0:LSQ_ENTRIES-1];
    logic [2:0]    lsq_f3   [0:LSQ_ENTRIES-1];
    logic [TW-1:0] lsq_tag  [0:LSQ_ENTRIES-1];

    logic          agu_any, st_any, ld_any;
    logic [LW-1:0] agu_idx, st_idx, ld_idx, q;
    always_comb begin
        agu_any = 1'b0;  agu_idx = '0;
        st_any  = 1'b0;  st_idx  = '0;
        ld_any  = 1'b0;  ld_idx  = '0;
        q       = '0;
        for (int i = 0; i < LSQ_ENTRIES; i++) begin
            q = lsq_head + i[LW-1:0];
            if (lsq_valid[q]) begin
                if (!agu_any && lsq_rb[q] && !lsq_av[q]) begin
                    agu_any = 1'b1;
                    agu_idx = q;
                end
                if (!st_any && lsq_store[q] && lsq_av[q] && lsq_rd[q] && !lsq_done[q]) begin
                    st_any = 1'b1;
                    st_idx = q;
                end
                if (!ld_any && !lsq_store[q] && lsq_av[q] && !lsq_done[q]) begin
                    ld_any = 1'b1;
                    ld_idx = q;
                end
            end
        end
    end

    // Address generation for the chosen entry.
    wire [63:0] agu_addr  = lsq_vb[agu_idx] + lsq_imm[agu_idx];
    wire [2:0]  agu_f3    = lsq_f3[agu_idx];
    wire [2:0]  agu_align = (agu_f3[1:0] == 2'd0) ? 3'b000 :
                            (agu_f3[1:0] == 2'd1) ? 3'b001 :
                            (agu_f3[1:0] == 2'd2) ? 3'b011 : 3'b111;
    wire        agu_mis   = ((agu_addr[2:0] & agu_align) != 3'b000);

    // The chosen load, and whether an older store stands in its way: one whose
    // address is not known yet, or one that writes bytes it reads.
    wire [63:0] ld_addr  = lsq_addr[ld_idx];
    wire [2:0]  ld_f3    = lsq_f3[ld_idx];
    wire [63:0] ld_bytes = 64'd1 << ld_f3[1:0];

    logic        ld_blocked, ld_reached;
    logic [LW-1:0] p;
    logic [63:0] st_a, st_n;
    always_comb begin
        ld_blocked = 1'b0;
        ld_reached = 1'b0;
        p    = '0;
        st_a = 64'b0;
        st_n = 64'b0;
        for (int i = 0; i < LSQ_ENTRIES; i++) begin
            p = lsq_head + i[LW-1:0];
            if (p == ld_idx) ld_reached = 1'b1;
            if (!ld_reached && lsq_valid[p] && lsq_store[p]) begin
                st_a = lsq_addr[p];
                st_n = 64'd1 << lsq_f3[p][1:0];
                if (!lsq_av[p])                                         ld_blocked = 1'b1;
                else if (st_a < ld_addr + ld_bytes && ld_addr < st_a + st_n) ld_blocked = 1'b1;
            end
        end
    end

    // ==================================================================
    // Retirement decisions
    // ==================================================================
    // The ROB head, and the entry behind it.
    wire [TW-1:0] h        = rob_head;
    wire          h_valid  = !rob_empty;
    wire [31:0]   h_instr  = rob_instr[h];
    wire [4:0]    h_rd     = h_instr[11:7];
    wire [TW-1:0] h2       = rob_head + 1'b1;
    wire [31:0]   h2_instr = rob_instr[h2];
    wire [4:0]    h2_rd    = h2_instr[11:7];

    // A store at the head writes memory now.
    wire        st_req   = h_valid && rob_store[h] && rob_done[h] && !rob_exc[h];
    wire [63:0] st_addr  = lsq_addr[lsq_head];
    wire [2:0]  st_f3    = lsq_f3[lsq_head];
    wire [63:0] st_mask  = (st_f3[1:0] == 2'd0) ? 64'h0000_0000_0000_00FF :
                           (st_f3[1:0] == 2'd1) ? 64'h0000_0000_0000_FFFF :
                           (st_f3[1:0] == 2'd2) ? 64'h0000_0000_FFFF_FFFF :
                                                  64'hFFFF_FFFF_FFFF_FFFF;
    wire [63:0] st_data  = lsq_vd[lsq_head] & st_mask;
    wire        st_fault = st_req && dmem_ready && dmem_fault;
    wire        st_ok    = st_req && dmem_ready && !dmem_fault;

    // The chosen load reads the cache when the store at the head is not using
    // the port.
    wire ld_mis  = lsq_mis[ld_idx];
    wire ld_read = ld_any && !ld_mis && !ld_blocked && !st_req;
    wire ld_done = ld_any && (ld_mis || (ld_read && dmem_ready));

    assign dmem_we    = st_req;
    assign dmem_re    = ld_read;
    assign dmem_addr  = st_req ? st_addr : ld_addr;
    assign dmem_size  = st_req ? st_f3[1:0] : ld_f3[1:0];
    assign dmem_wdata = st_data;
    assign dmem_pc    = st_req ? rob_pc[h] : rob_pc[lsq_tag[ld_idx]];

    // Sign- or zero-extend the loaded value. funct3[2] set means unsigned.
    wire [7:0]  ld_b = dmem_rdata[7:0];
    wire [15:0] ld_h = dmem_rdata[15:0];
    wire [31:0] ld_w = dmem_rdata[31:0];
    wire        ld_unsigned = ld_f3[2];
    wire [63:0] ld_value =
        (ld_f3[1:0] == 2'd0) ? {{56{ld_b[7]  & ~ld_unsigned}}, ld_b} :
        (ld_f3[1:0] == 2'd1) ? {{48{ld_h[15] & ~ld_unsigned}}, ld_h} :
        (ld_f3[1:0] == 2'd2) ? {{32{ld_w[31] & ~ld_unsigned}}, ld_w} :
                               dmem_rdata;

    wire        trap       = h_valid && ((rob_done[h] && rob_exc[h]) || st_fault);
    wire [3:0]  trap_cause = st_fault ? CAUSE_STORE_ACCESS : rob_cause[h];
    wire [63:0] trap_tval  = st_fault ? st_addr : rob_tval[h];

    wire sys_fire   = h_valid && rob_sys[h];            // executes and retires at once
    wire do_mret    = sys_fire && rob_mret[h];
    wire csr_fire   = sys_fire && !rob_mret[h];
    wire plain_fire = h_valid && rob_done[h] && !rob_exc[h] && !rob_store[h] && !rob_sys[h];
    wire retire     = sys_fire || st_ok || plain_fire;
    wire h_mis      = retire && rob_mis[h] && !do_mret;

    // The entry behind the head goes with it if the head retires and fetch
    // carries on after it, and the entry itself needs nothing special.
    wire retire2    = (WIDTH == 2) && retire && !h_mis && !do_mret && (rob_count >= 2)
                   && rob_done[h2] && !rob_exc[h2] && !rob_store[h2] && !rob_sys[h2];
    wire h2_mis     = retire2 && rob_mis[h2];

    logic [63:0] csr_rdata;
    logic [63:0] mtvec, mepc;
    wire [1:0]   h_csr_op = h_instr[13:12];

    csr_file u_csr (
        .clk        (clk),
        .rst        (rst),
        .csr_en     (csr_fire),
        .csr_addr   (h_instr[31:20]),
        .csr_op     (h_csr_op),
        .csr_write  ((h_csr_op == 2'b01) || (h_instr[19:15] != 5'd0)),
        .csr_src    (rob_value[h]),
        .csr_rdata  (csr_rdata),
        .trap       (trap),
        .trap_pc    (rob_pc[h]),
        .trap_cause (trap_cause),
        .trap_tval  (trap_tval),
        .mret       (do_mret),
        .retire     (retire),
        .retire2    (retire2),
        .mtvec      (mtvec),
        .mepc       (mepc)
    );

    assign flush    = trap || do_mret || h_mis || h2_mis;
    assign flush_pc = trap ? mtvec : do_mret ? mepc : h_mis ? rob_next[h] : rob_next[h2];

    // The register file takes both results; if they are for the same register
    // the younger one is the one that stays.
    wire        arf_we    = retire && rob_wr[h];
    wire [63:0] arf_wdata = rob_sys[h] ? csr_rdata : rob_value[h];
    wire        arf2_we   = retire2 && rob_wr[h2];
    always_ff @(posedge clk) begin
        if (arf_we)  arf[h_rd]  <= arf_wdata;
        if (arf2_we) arf[h2_rd] <= rob_value[h2];
    end

    assign commit_valid    = retire || trap;
    assign commit_pc       = rob_pc[h];
    assign commit_instr    = h_instr;
    assign commit_trap     = trap;
    assign commit_cause    = trap_cause;
    assign commit_tval     = trap_tval;
    assign commit_rd_we    = arf_we;
    assign commit_rd       = h_rd;
    assign commit_rd_data  = arf_wdata;
    assign commit_mem_we   = st_ok;
    assign commit_mem_addr = st_addr;
    assign commit_mem_size = st_f3[1:0];
    assign commit_mem_data = st_data;

    assign commit2_valid   = retire2;
    assign commit2_pc      = rob_pc[h2];
    assign commit2_instr   = h2_instr;
    assign commit2_rd_we   = arf2_we;
    assign commit2_rd      = h2_rd;
    assign commit2_rd_data = rob_value[h2];

    // The predictor learns from retired instructions only.
    assign bp_train   = retire;
    assign bp_pc      = rob_pc[h];
    assign bp_taken   = rob_taken[h];
    assign bp_target  = rob_next[h];
    assign bp2_train  = retire2;
    assign bp2_pc     = rob_pc[h2];
    assign bp2_taken  = rob_taken[h2];
    assign bp2_target = rob_next[h2];

    // ==================================================================
    // Execute (ALU, branches, jumps)
    // ==================================================================
    // Unit 0 takes the oldest ready station, unit 1 the next oldest.
    logic [1:0] x_fire;
    assign x_fire[0] = sel_any[0] && ((WIDTH == 2) || !ld_done);
    assign x_fire[1] = sel_any[1] && (WIDTH == 2) && !ld_done;

    logic [TW-1:0] x_tag    [0:1];
    logic [63:0]   x_value  [0:1];
    logic [63:0]   x_next   [0:1];
    logic [63:0]   x_target [0:1];
    logic [1:0]    x_transfers, x_mis, x_misalign;

    for (genvar u = 0; u < 2; u++) begin : g_exec
        ctrl_t xc;
        assign xc = rs_ctrl[sel[u]];
        wire [63:0] value, next, target;
        wire        transfers, mis, misalign;

        exec_unit u_exec (
            .ctrl      (xc),
            .v1        (rs_v1[sel[u]]),
            .v2        (rs_v2[sel[u]]),
            .imm       (rs_imm[sel[u]]),
            .pc        (rs_pc[sel[u]]),
            .pred      (rs_pred[sel[u]]),
            .value     (value),
            .next      (next),
            .transfers (transfers),
            .mis       (mis),
            .misalign  (misalign),
            .target    (target)
        );

        assign x_tag[u]       = rs_tag[sel[u]];
        assign x_value[u]     = value;
        assign x_next[u]      = next;
        assign x_target[u]    = target;
        assign x_transfers[u] = transfers;
        assign x_mis[u]       = mis;
        assign x_misalign[u]  = misalign;
    end

    // ---- the buses ----
    // Bus 0: the load if one completes, otherwise unit 0.
    assign cdb_valid[0] = ld_done || x_fire[0];
    assign cdb_tag[0]   = ld_done ? lsq_tag[ld_idx] : x_tag[0];
    assign cdb_value[0] = ld_done ? ld_value : x_value[0];
    assign cdb_exc[0]   = ld_done ? (ld_mis || dmem_fault) : x_misalign[0];
    assign cdb_cause[0] = !ld_done ? CAUSE_INSTR_MISALIGNED :
                          ld_mis   ? CAUSE_LOAD_MISALIGNED : CAUSE_LOAD_ACCESS;
    assign cdb_tval[0]  = ld_done ? ld_addr : x_target[0];

    // Bus 1: unit 0 if the load has bus 0, otherwise unit 1.
    assign cdb_valid[1] = ld_done ? x_fire[0]     : x_fire[1];
    assign cdb_tag[1]   = ld_done ? x_tag[0]      : x_tag[1];
    assign cdb_value[1] = ld_done ? x_value[0]    : x_value[1];
    assign cdb_exc[1]   = ld_done ? x_misalign[0] : x_misalign[1];
    assign cdb_cause[1] = CAUSE_INSTR_MISALIGNED;
    assign cdb_tval[1]  = ld_done ? x_target[0]   : x_target[1];

    // ==================================================================
    // Dispatch decision
    // ==================================================================
    logic [1:0] d_block;
    assign d_block[0] = rob_full
                || (d_exec[0] && !rs_any0)
                || (d_mem[0] && lsq_full)
                || (d_sys[0] && !rob_empty)             // wait for the ROB to drain
                || (!rob_empty && rob_sys[rob_head]);   // nothing follows it in
    // The second instruction needs room beside the first.
    assign d_block[1] = (WIDTH != 2) || !rob_two
                || d_sys[0] || d_sys[1]
                || (d_exec[1] && !(d_exec[0] ? rs_any1 : rs_any0))
                || (d_mem[1] && (lsq_count + d_mem[0] >= LSQ_ENTRIES));
    assign disp[0] = fd_valid[0] && !d_block[0] && !flush;
    assign disp[1] = disp[0] && fd_valid[1] && !d_block[1];

    // What each slot is given: a ROB entry, and a reservation station or a
    // queue entry if it needs one.
    logic [TW-1:0] dp_t  [0:1];
    logic [31:0]   dp_rs [0:1];
    logic [LW-1:0] dp_l  [0:1];
    assign dp_t[0]  = rob_tail;
    assign dp_t[1]  = rob_tail + 1'b1;
    assign dp_rs[0] = rs_free0;
    assign dp_rs[1] = d_exec[0] ? rs_free1 : rs_free0;
    assign dp_l[0]  = lsq_tail;
    assign dp_l[1]  = d_mem[0] ? lsq_tail + 1'b1 : lsq_tail;

    // ==================================================================
    // State update
    // ==================================================================
    wire pop_a = retire && (rob_store[h] || rob_load[h]);
    wire pop_b = retire2 && rob_load[h2];
    wire [LW-1:0] lsq_second = lsq_head + 1'b1;

    always_ff @(posedge clk) begin
        if (rst || flush) begin
            // Empty everything.
            rob_head  <= '0;
            rob_tail  <= '0;
            rob_count <= '0;
            rob_done  <= '0;
            rob_exc   <= '0;
            rob_sys   <= '0;
            rat_busy  <= '0;
            rs_valid  <= '0;
            lsq_head  <= '0;
            lsq_tail  <= '0;
            lsq_count <= '0;
            lsq_valid <= '0;
        end else begin
            // ---- results on the buses ----
            for (int b = 0; b < 2; b++) begin
                if (cdb_valid[b]) begin
                    rob_done[cdb_tag[b]]  <= 1'b1;
                    rob_value[cdb_tag[b]] <= cdb_value[b];
                    if (cdb_exc[b]) begin
                        rob_exc[cdb_tag[b]]   <= 1'b1;
                        rob_cause[cdb_tag[b]] <= cdb_cause[b];
                        rob_tval[cdb_tag[b]]  <= cdb_tval[b];
                    end
                    // wake up everything waiting for this tag
                    for (int k = 0; k < RS_ENTRIES; k++) begin
                        if (rs_valid[k] && !rs_r1[k] && rs_t1[k] == cdb_tag[b]) begin
                            rs_r1[k] <= 1'b1;
                            rs_v1[k] <= cdb_value[b];
                        end
                        if (rs_valid[k] && !rs_r2[k] && rs_t2[k] == cdb_tag[b]) begin
                            rs_r2[k] <= 1'b1;
                            rs_v2[k] <= cdb_value[b];
                        end
                    end
                    for (int k = 0; k < LSQ_ENTRIES; k++) begin
                        if (lsq_valid[k] && !lsq_rb[k] && lsq_tb[k] == cdb_tag[b]) begin
                            lsq_rb[k] <= 1'b1;
                            lsq_vb[k] <= cdb_value[b];
                        end
                        if (lsq_valid[k] && !lsq_rd[k] && lsq_td[k] == cdb_tag[b]) begin
                            lsq_rd[k] <= 1'b1;
                            lsq_vd[k] <= cdb_value[b];
                        end
                    end
                end
            end

            // ---- execution units: the station is freed; what the branch did ----
            for (int u = 0; u < 2; u++) begin
                if (x_fire[u]) begin
                    rs_valid[sel[u]]     <= 1'b0;
                    rob_mis[x_tag[u]]    <= x_mis[u];
                    rob_next[x_tag[u]]   <= x_next[u];
                    rob_taken[x_tag[u]]  <= x_transfers[u];
                end
            end

            // ---- load/store queue ----
            if (agu_any) begin
                lsq_av[agu_idx]   <= 1'b1;
                lsq_addr[agu_idx] <= agu_addr;
                lsq_mis[agu_idx]  <= agu_mis;
            end
            if (st_any) begin
                // Address and data are known: the store may retire.
                lsq_done[st_idx]         <= 1'b1;
                rob_done[lsq_tag[st_idx]] <= 1'b1;
                if (lsq_mis[st_idx]) begin
                    rob_exc[lsq_tag[st_idx]]   <= 1'b1;
                    rob_cause[lsq_tag[st_idx]] <= CAUSE_STORE_MISALIGNED;
                    rob_tval[lsq_tag[st_idx]]  <= lsq_addr[st_idx];
                end
            end
            if (ld_done) lsq_done[ld_idx] <= 1'b1;

            // ---- retire ----
            if (retire) begin
                if (rob_wr[h] && rat_busy[h_rd] && rat_tag[h_rd] == h) rat_busy[h_rd] <= 1'b0;
                rob_sys[h] <= 1'b0;
            end
            if (retire2) begin
                if (rob_wr[h2] && rat_busy[h2_rd] && rat_tag[h2_rd] == h2) rat_busy[h2_rd] <= 1'b0;
            end
            rob_head <= rob_head + TW'(retire) + TW'(retire2);

            if (pop_a || pop_b)  lsq_valid[lsq_head]   <= 1'b0;
            if (pop_a && pop_b)  lsq_valid[lsq_second] <= 1'b0;
            lsq_head <= lsq_head + LW'(pop_a) + LW'(pop_b);

            // ---- dispatch (slot 0 first, so that slot 1 has the last word on
            // the RAT if both write the same register) ----
            for (int s = 0; s < 2; s++) begin
                if (disp[s]) begin
                    rob_pc[dp_t[s]]     <= fd_pc[s];
                    rob_instr[dp_t[s]]  <= fd_instr[s];
                    rob_wr[dp_t[s]]     <= d_writes[s];
                    rob_store[dp_t[s]]  <= d_store[s];
                    rob_load[dp_t[s]]   <= d_load[s];
                    rob_sys[dp_t[s]]    <= d_sys[s];
                    rob_mret[dp_t[s]]   <= d_mret[s];
                    rob_branch[dp_t[s]] <= d_branch[s];
                    rob_jump[dp_t[s]]   <= d_jump[s];
                    rob_exc[dp_t[s]]    <= d_exc[s];
                    rob_cause[dp_t[s]]  <= d_cause[s];
                    rob_tval[dp_t[s]]   <= d_tval[s];
                    // Nothing to execute: complete already.
                    rob_done[dp_t[s]]   <= d_exc[s] || d_nop[s];
                    rob_value[dp_t[s]]  <= d_csr_src[s];
                    // The real next pc is pc + 4 unless an execution unit finds
                    // a taken branch or jump.
                    rob_next[dp_t[s]]   <= fd_pc[s] + 64'd4;
                    rob_taken[dp_t[s]]  <= 1'b0;
                    rob_mis[dp_t[s]]    <= !d_exec[s] && (fd_pred[s] != fd_pc[s] + 64'd4);

                    if (d_writes[s]) begin
                        rat_busy[d_rd[s]] <= 1'b1;
                        rat_tag[d_rd[s]]  <= dp_t[s];
                    end

                    if (d_exec[s]) begin
                        rs_valid[dp_rs[s]] <= 1'b1;
                        rs_ctrl[dp_rs[s]]  <= d_ctrl[s];
                        rs_imm[dp_rs[s]]   <= d_imm[s];
                        rs_pc[dp_rs[s]]    <= fd_pc[s];
                        rs_pred[dp_rs[s]]  <= fd_pred[s];
                        rs_tag[dp_rs[s]]   <= dp_t[s];
                        rs_r1[dp_rs[s]]    <= op1_ready[s];
                        rs_v1[dp_rs[s]]    <= op1_val[s];
                        rs_t1[dp_rs[s]]    <= op1_tag[s];
                        rs_r2[dp_rs[s]]    <= op2_ready[s];
                        rs_v2[dp_rs[s]]    <= op2_val[s];
                        rs_t2[dp_rs[s]]    <= op2_tag[s];
                    end

                    if (d_mem[s]) begin
                        lsq_valid[dp_l[s]] <= 1'b1;
                        lsq_store[dp_l[s]] <= d_store[s];
                        lsq_f3[dp_l[s]]    <= d_f3[s];
                        lsq_imm[dp_l[s]]   <= d_imm[s];
                        lsq_tag[dp_l[s]]   <= dp_t[s];
                        lsq_rb[dp_l[s]]    <= op1_ready[s];
                        lsq_vb[dp_l[s]]    <= op1_val[s];
                        lsq_tb[dp_l[s]]    <= op1_tag[s];
                        lsq_rd[dp_l[s]]    <= op2_ready[s];
                        lsq_vd[dp_l[s]]    <= op2_val[s];
                        lsq_td[dp_l[s]]    <= op2_tag[s];
                        lsq_av[dp_l[s]]    <= 1'b0;
                        lsq_mis[dp_l[s]]   <= 1'b0;
                        lsq_done[dp_l[s]]  <= 1'b0;
                    end
                end
            end
            rob_tail <= rob_tail + TW'(disp[0]) + TW'(disp[1]);
            lsq_tail <= lsq_tail + LW'(disp[0] && d_mem[0]) + LW'(disp[1] && d_mem[1]);

            rob_count <= rob_count + disp[0] + disp[1] - retire - retire2;
            lsq_count <= lsq_count + (disp[0] && d_mem[0]) + (disp[1] && d_mem[1])
                                   - pop_a - pop_b;
        end
    end

    // ==================================================================
    // Invariants (simulation only)
    // ==================================================================
    // Conditions that must hold in every cycle whatever the program does.
    initial begin
        if (WIDTH != 1 && WIDTH != 2) begin
            $display("ERROR: WIDTH must be 1 or 2");
            $finish;
        end
    end

    integer n_lsq;
    always_ff @(posedge clk) begin
        if (!rst) begin
            n_lsq = 0;
            for (int k = 0; k < LSQ_ENTRIES; k++) n_lsq = n_lsq + (lsq_valid[k] ? 1 : 0);

            if (rat_busy[0]) begin
                $display("ERROR: invariant broken: register x0 is renamed");
                $finish;
            end
            if (rob_count > ROB_ENTRIES ||
                (rob_full ? (rob_tail != rob_head)
                          : (rob_count[TW-1:0] != TW'(rob_tail - rob_head)))) begin
                $display("ERROR: invariant broken: reorder buffer pointers and count disagree");
                $finish;
            end
            if (n_lsq != lsq_count) begin
                $display("ERROR: invariant broken: load/store queue count and entries disagree");
                $finish;
            end
            for (int b = 0; b < 2; b++) begin
                if (cdb_valid[b] && rob_done[cdb_tag[b]]) begin
                    $display("ERROR: invariant broken: a result arrived for an entry that is already done");
                    $finish;
                end
            end
            if (cdb_valid[0] && cdb_valid[1] && cdb_tag[0] == cdb_tag[1]) begin
                $display("ERROR: invariant broken: both buses carry a result for the same entry");
                $finish;
            end
            if (WIDTH == 1 && (cdb_valid[1] || disp[1] || retire2 || fd_valid[1])) begin
                $display("ERROR: invariant broken: the second slot is in use in a core of width 1");
                $finish;
            end
            if (fd_valid == 2'b10 || (disp[1] && !disp[0]) || (retire2 && !retire)) begin
                $display("ERROR: invariant broken: a younger instruction moved ahead of an older one");
                $finish;
            end
            if (h_valid && rob_sys[h] && rob_count != 1) begin
                $display("ERROR: invariant broken: a system instruction is not alone in the reorder buffer");
                $finish;
            end
            if (retire && trap) begin
                $display("ERROR: invariant broken: an instruction retired and trapped at once");
                $finish;
            end
            if ((pop_a || pop_b) && !lsq_valid[lsq_head] ||
                (pop_a && pop_b) && !lsq_valid[lsq_second]) begin
                $display("ERROR: invariant broken: a memory instruction retired without a queue entry");
                $finish;
            end
        end
    end

    // ==================================================================
    // Counters
    // ==================================================================
    assign cnt_loaduse = 64'd0;

    always_ff @(posedge clk) begin
        if (rst) begin
            cnt_redirect   <= 64'd0;
            cnt_flush      <= 64'd0;
            cnt_ifwait     <= 64'd0;
            cnt_hold       <= 64'd0;
            cnt_branch     <= 64'd0;
            cnt_branch_mis <= 64'd0;
            cnt_jump       <= 64'd0;
            cnt_jump_mis   <= 64'd0;
        end else begin
            if (trap || do_mret)      cnt_flush    <= cnt_flush + 64'd1;
            else if (h_mis || h2_mis) cnt_redirect <= cnt_redirect + 64'd1;
            if (!flush && !fd_left && if_wait) cnt_ifwait <= cnt_ifwait + 64'd1;
            if ((dmem_re || dmem_we) && !dmem_ready) cnt_hold <= cnt_hold + 64'd1;
            cnt_branch     <= cnt_branch     + (retire && rob_branch[h])
                                             + (retire2 && rob_branch[h2]);
            cnt_branch_mis <= cnt_branch_mis + (retire && rob_branch[h] && rob_mis[h])
                                             + (retire2 && rob_branch[h2] && rob_mis[h2]);
            cnt_jump       <= cnt_jump       + (retire && rob_jump[h])
                                             + (retire2 && rob_jump[h2]);
            cnt_jump_mis   <= cnt_jump_mis   + (retire && rob_jump[h] && rob_mis[h])
                                             + (retire2 && rob_jump[h2] && rob_mis[h2]);
        end
    end
endmodule
