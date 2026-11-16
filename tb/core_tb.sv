// Testbench for the new cores. Connects a core to the memory subsystem (caches)
// and the memory model, loads a program, runs it until it halts, and writes the
// commit trace that tools/regress.py compares with the golden model.

`timescale 1ns / 1ps

`ifndef CORE
  `define CORE inorder_core
`endif

module core_tb;
    import rv_pkg::*;

    parameter int LINE_BYTES = 32;
    parameter int IC_ENABLE  = 0;
    parameter int IC_SETS    = 32;
    parameter int IC_WAYS    = 1;
    parameter int DC_ENABLE  = 0;
    parameter int DC_SETS    = 32;
    parameter int DC_WAYS    = 1;
    parameter int IC_REPL         = 0;
    parameter int DC_REPL         = 0;
    parameter int DC_WRITE_HIT    = 0;
    parameter int DC_WRITE_MISS   = 0;
    parameter int DC_WBUF_ENTRIES = 0;
    parameter int DC_WBUF_MERGE   = 0;
    parameter int IC_VICTIM_ENTRIES = 0;
    parameter int DC_VICTIM_ENTRIES = 0;
    parameter int L2_ENABLE       = 0;
    parameter int L2_SETS         = 64;
    parameter int L2_WAYS         = 4;
    parameter int L2_REPL         = 0;
    parameter int L2_WRITE_HIT    = 0;
    parameter int L2_WRITE_MISS   = 0;
    parameter int L2_WBUF_ENTRIES = 0;
    parameter int L2_WBUF_MERGE   = 0;
    parameter int L2_HIT_LATENCY  = 4;
    parameter int L2_HIT_JITTER   = 0;
    parameter int L2_INCLUSION    = 0;
    parameter int IC_PF_MODE  = 0;
    parameter int DC_PF_MODE  = 0;
    parameter int PF_DISTANCE = 4;
    parameter int BP_MODE         = 0;
    parameter int BP_ENTRIES      = 64;
    parameter int BP_TAG_BITS     = 0;
    parameter int BP_COUNTER_BITS = 2;
    parameter int WIDTH       = 1;
    parameter int ROB_ENTRIES = 16;
    parameter int RS_ENTRIES  = 8;
    parameter int LSQ_ENTRIES = 8;

    localparam int LINE_BITS = 8 * LINE_BYTES;
    localparam int RAM_BYTES = 65536;
    localparam int OFF_BITS  = $clog2(LINE_BYTES);

    logic clk = 1'b0;
    logic rst = 1'b1;
    always #5 clk = ~clk;

    // core <-> memory subsystem
    logic        imem_req, imem_ready, imem_fault;
    logic [63:0] imem_addr;
    logic [31:0] imem_rdata, imem_rdata2;
    logic        imem_valid2;
    logic        dmem_re, dmem_we, dmem_ready, dmem_fault;
    logic [63:0] dmem_addr, dmem_wdata, dmem_rdata, dmem_pc;
    logic [1:0]  dmem_size;

    // memory subsystem <-> memory
    logic                  ma_req, ma_resp;
    logic [63:0]           ma_addr;
    logic [LINE_BITS-1:0]  ma_rdata;
    logic                  mb_req, mb_we, mb_resp;
    logic [63:0]           mb_addr;
    logic [LINE_BITS-1:0]  mb_wdata, mb_rdata;
    logic [LINE_BYTES-1:0] mb_wstrb;

    logic        commit_valid, commit_trap, commit_rd_we, commit_mem_we;
    logic [63:0] commit_pc, commit_tval, commit_rd_data, commit_mem_addr, commit_mem_data;
    logic [3:0]  commit_cause;
    logic [31:0] commit_instr;
    logic [4:0]  commit_rd;
    logic [1:0]  commit_mem_size;
    // The second instruction retired in a cycle.
    logic        commit2_valid, commit2_rd_we;
    logic [63:0] commit2_pc, commit2_rd_data;
    logic [31:0] commit2_instr;
    logic [4:0]  commit2_rd;
`ifndef CORE_OOO
    assign commit2_valid   = 1'b0;
    assign commit2_rd_we   = 1'b0;
    assign commit2_pc      = 64'b0;
    assign commit2_rd_data = 64'b0;
    assign commit2_instr   = 32'b0;
    assign commit2_rd      = 5'b0;
`endif
    logic [63:0] cnt_redirect, cnt_loaduse, cnt_flush, cnt_ifwait, cnt_hold;
    logic [63:0] cnt_branch, cnt_branch_mis, cnt_jump, cnt_jump_mis;
    logic [63:0] ic_access, ic_miss, dc_access, dc_miss, dc_writeback;
    logic [63:0] ic_pf_issued, ic_pf_useful, ic_pf_late;
    logic [63:0] dc_pf_issued, dc_pf_useful, dc_pf_late;
    logic [63:0] dc_evict, dc_mem_read, dc_mem_write, dc_wbuf_full;
    logic [63:0] ic_wait, dc_wait, ic_vb_hit, dc_vb_hit, ic_inval, dc_inval;
    logic [63:0] ic_evict, ic_mem_read;
    logic [63:0] l2_access, l2_miss, l2_writeback, l2_evict, l2_mem_read, l2_mem_write;
    logic [63:0] l2_wait, ic_arb_wait, dc_arb_wait, l2_store_miss, dc_store_miss;

    `CORE #(
        .BP_MODE         (BP_MODE),
        .BP_ENTRIES      (BP_ENTRIES),
        .BP_TAG_BITS     (BP_TAG_BITS),
        .BP_COUNTER_BITS (BP_COUNTER_BITS)
`ifdef CORE_OOO
        ,
        .WIDTH           (WIDTH),
        .ROB_ENTRIES     (ROB_ENTRIES),
        .RS_ENTRIES      (RS_ENTRIES),
        .LSQ_ENTRIES     (LSQ_ENTRIES)
`endif
    ) u_core (
        .clk             (clk),
        .rst             (rst),
        .imem_req        (imem_req),
        .imem_addr       (imem_addr),
        .imem_rdata      (imem_rdata),
        .imem_ready      (imem_ready),
        .imem_fault      (imem_fault),
        .dmem_re         (dmem_re),
        .dmem_we         (dmem_we),
        .dmem_addr       (dmem_addr),
        .dmem_pc         (dmem_pc),
        .dmem_size       (dmem_size),
        .dmem_wdata      (dmem_wdata),
        .dmem_rdata      (dmem_rdata),
        .dmem_ready      (dmem_ready),
        .dmem_fault      (dmem_fault),
        .commit_valid    (commit_valid),
        .commit_pc       (commit_pc),
        .commit_instr    (commit_instr),
        .commit_trap     (commit_trap),
        .commit_cause    (commit_cause),
        .commit_tval     (commit_tval),
        .commit_rd_we    (commit_rd_we),
        .commit_rd       (commit_rd),
        .commit_rd_data  (commit_rd_data),
        .commit_mem_we   (commit_mem_we),
        .commit_mem_addr (commit_mem_addr),
        .commit_mem_size (commit_mem_size),
        .commit_mem_data (commit_mem_data),
`ifdef CORE_OOO
        .imem_rdata2     (imem_rdata2),
        .imem_valid2     (imem_valid2),
        .commit2_valid   (commit2_valid),
        .commit2_pc      (commit2_pc),
        .commit2_instr   (commit2_instr),
        .commit2_rd_we   (commit2_rd_we),
        .commit2_rd      (commit2_rd),
        .commit2_rd_data (commit2_rd_data),
`endif
        .cnt_redirect    (cnt_redirect),
        .cnt_loaduse     (cnt_loaduse),
        .cnt_flush       (cnt_flush),
        .cnt_ifwait      (cnt_ifwait),
        .cnt_hold        (cnt_hold),
        .cnt_branch      (cnt_branch),
        .cnt_branch_mis  (cnt_branch_mis),
        .cnt_jump        (cnt_jump),
        .cnt_jump_mis    (cnt_jump_mis)
    );

    mem_subsystem #(
        .LINE_BYTES (LINE_BYTES),
        .IC_ENABLE  (IC_ENABLE),
        .IC_SETS    (IC_SETS),
        .IC_WAYS    (IC_WAYS),
        .DC_ENABLE  (DC_ENABLE),
        .DC_SETS    (DC_SETS),
        .DC_WAYS    (DC_WAYS),
        .IC_REPL         (IC_REPL),
        .DC_REPL         (DC_REPL),
        .DC_WRITE_HIT    (DC_WRITE_HIT),
        .DC_WRITE_MISS   (DC_WRITE_MISS),
        .DC_WBUF_ENTRIES (DC_WBUF_ENTRIES),
        .DC_WBUF_MERGE   (DC_WBUF_MERGE),
        .IC_VICTIM_ENTRIES (IC_VICTIM_ENTRIES),
        .DC_VICTIM_ENTRIES (DC_VICTIM_ENTRIES),
        .L2_ENABLE       (L2_ENABLE),
        .L2_SETS         (L2_SETS),
        .L2_WAYS         (L2_WAYS),
        .L2_REPL         (L2_REPL),
        .L2_WRITE_HIT    (L2_WRITE_HIT),
        .L2_WRITE_MISS   (L2_WRITE_MISS),
        .L2_WBUF_ENTRIES (L2_WBUF_ENTRIES),
        .L2_WBUF_MERGE   (L2_WBUF_MERGE),
        .L2_HIT_LATENCY  (L2_HIT_LATENCY),
        .L2_HIT_JITTER   (L2_HIT_JITTER),
        .L2_INCLUSION    (L2_INCLUSION),
        .IC_PF_MODE  (IC_PF_MODE),
        .DC_PF_MODE  (DC_PF_MODE),
        .PF_DISTANCE (PF_DISTANCE)
    ) u_memsys (
        .clk          (clk),
        .rst          (rst),
        .imem_req     (imem_req),
        .imem_addr    (imem_addr),
        .imem_rdata   (imem_rdata),
        .imem_ready   (imem_ready),
        .imem_fault   (imem_fault),
        .imem_rdata2  (imem_rdata2),
        .imem_valid2  (imem_valid2),
        .dmem_re      (dmem_re),
        .dmem_we      (dmem_we),
        .dmem_addr    (dmem_addr),
        .dmem_pc      (dmem_pc),
        .dmem_size    (dmem_size),
        .dmem_wdata   (dmem_wdata),
        .dmem_rdata   (dmem_rdata),
        .dmem_ready   (dmem_ready),
        .dmem_fault   (dmem_fault),
        .ma_req       (ma_req),
        .ma_addr      (ma_addr),
        .ma_resp      (ma_resp),
        .ma_rdata     (ma_rdata),
        .mb_req       (mb_req),
        .mb_we        (mb_we),
        .mb_addr      (mb_addr),
        .mb_wdata     (mb_wdata),
        .mb_wstrb     (mb_wstrb),
        .mb_resp      (mb_resp),
        .mb_rdata     (mb_rdata),
        .ic_access    (ic_access),
        .ic_miss      (ic_miss),
        .dc_access    (dc_access),
        .dc_miss      (dc_miss),
        .dc_writeback (dc_writeback),
        .ic_pf_issued (ic_pf_issued),
        .ic_pf_useful (ic_pf_useful),
        .ic_pf_late   (ic_pf_late),
        .dc_pf_issued (dc_pf_issued),
        .dc_pf_useful (dc_pf_useful),
        .dc_pf_late   (dc_pf_late),
        .dc_evict     (dc_evict),
        .dc_mem_read  (dc_mem_read),
        .dc_mem_write (dc_mem_write),
        .dc_wbuf_full (dc_wbuf_full),
        .ic_wait      (ic_wait),
        .dc_wait      (dc_wait),
        .ic_vb_hit    (ic_vb_hit),
        .dc_vb_hit    (dc_vb_hit),
        .ic_inval     (ic_inval),
        .dc_inval     (dc_inval),
        .ic_evict     (ic_evict),
        .ic_mem_read  (ic_mem_read),
        .l2_access    (l2_access),
        .l2_miss      (l2_miss),
        .l2_store_miss (l2_store_miss),
        .dc_store_miss (dc_store_miss),
        .l2_writeback (l2_writeback),
        .l2_evict     (l2_evict),
        .l2_mem_read  (l2_mem_read),
        .l2_mem_write (l2_mem_write),
        .l2_wait      (l2_wait),
        .ic_arb_wait  (ic_arb_wait),
        .dc_arb_wait  (dc_arb_wait)
    );

    sim_memory #(
        .LINE_BYTES (LINE_BYTES)
    ) u_mem (
        .clk     (clk),
        .rst     (rst),
        .a_req   (ma_req),
        .a_addr  (ma_addr),
        .a_resp  (ma_resp),
        .a_rdata (ma_rdata),
        .b_req   (mb_req),
        .b_we    (mb_we),
        .b_addr  (mb_addr),
        .b_wdata (mb_wdata),
        .b_wstrb (mb_wstrb),
        .b_resp  (mb_resp),
        .b_rdata (mb_rdata)
    );

    string  hex_file, trace_file, vcd_file, log_file, dump_file;
    integer trace_fd = 0;
    integer max_cycles;
    longint max_events;
    longint cycles  = 0;
    longint instret = 0;
    longint traps   = 0;
    logic   halted  = 1'b0;

    // The final contents of memory.
    logic [63:0]           settle_base;
    logic [LINE_BITS-1:0]  settle_line;
    logic [LINE_BYTES-1:0] settle_strb;
    integer                settle_slot;

    task automatic settle_put();
        for (int b = 0; b < LINE_BYTES; b++)
            if (settle_strb[b]) u_mem.ram[settle_base[15:0] + b] = settle_line[8*b +: 8];
    endtask

    // C: a cache instance; LINES_N: lines in its array; VB_N: entries of its
    // victim buffer.
    `define SETTLE_CACHE(C, LINES_N, VB_N) \
        if (C.sim_evicting) begin \
            settle_base = C.evict_addr; \
            settle_line = C.evict_line; \
            settle_strb = '1; \
            settle_put(); \
        end \
        for (int i = 0; i < C.wb_count; i++) begin \
            settle_slot = (C.wb_head + i) % C.WB_N; \
            settle_base = C.wb_addr[settle_slot]; \
            settle_line = C.wb_data[settle_slot]; \
            settle_strb = C.wb_strb[settle_slot]; \
            settle_put(); \
        end \
        for (int e = 0; e < (LINES_N); e++) begin \
            if (C.valid[e] && C.dirty[e]) begin \
                settle_base = C.sim_addr(e); \
                settle_line = C.lines[e]; \
                settle_strb = '1; \
                settle_put(); \
            end \
        end \
        for (int k = 0; k < (VB_N); k++) begin \
            if (C.vb_valid[k] && C.vb_dirty[k]) begin \
                settle_base = C.vb_addr[k]; \
                settle_line = C.vb_line[k]; \
                settle_strb = '1; \
                settle_put(); \
            end \
        end

    // The second-level cache only exists when it is enabled, so everything that
    // looks inside it is generated on the same condition.
    string l2_log_file;
    generate
        if (L2_ENABLE != 0) begin : g_hier
            task automatic settle();
                `SETTLE_CACHE(u_memsys.g_l2.u_l2, L2_SETS * L2_WAYS, 0)
            endtask
            function automatic integer inclusion_errors();
                return u_memsys.g_l2.inclusion_errors();
            endfunction
            initial begin
                if ($value$plusargs("l2cache_log=%s", l2_log_file))
                    u_memsys.g_l2.u_l2.log_fd = $fopen(l2_log_file, "w");
            end
        end else begin : g_hier
            task automatic settle();
            endtask
            function automatic integer inclusion_errors();
                return 0;
            endfunction
        end
    endgenerate

    task automatic settle_memory();
        g_hier.settle();
        if (DC_ENABLE != 0) begin
            `SETTLE_CACHE(u_memsys.u_dcache, DC_SETS * DC_WAYS, DC_VICTIM_ENTRIES)
        end
    endtask

    // Checksum of memory, eight bytes at a time (rvsim.memory_hash is the same
    // computation).
    function automatic logic [63:0] memory_hash();
        logic [63:0] h, word;
        h = 64'hcbf29ce484222325;
        for (int i = 0; i < RAM_BYTES; i += 8) begin
            for (int b = 0; b < 8; b++) word[8*b +: 8] = u_mem.ram[i + b];
            h = (h ^ word) * 64'h00000100000001b3;
        end
        return h;
    endfunction

    task automatic finish_run(input string status, input logic [63:0] exit_code);
        $display("STATS status=%s exit=%0d cycles=%0d instret=%0d traps=%0d redirects=%0d loaduse=%0d flushes=%0d ifwait=%0d hold=%0d ic_access=%0d ic_miss=%0d dc_access=%0d dc_miss=%0d dc_writeback=%0d branches=%0d branch_mis=%0d jumps=%0d jump_mis=%0d ic_pf_issued=%0d ic_pf_useful=%0d ic_pf_late=%0d dc_pf_issued=%0d dc_pf_useful=%0d dc_pf_late=%0d dc_evict=%0d dc_mem_read=%0d dc_mem_write=%0d dc_wbuf_full=%0d",
                 status, exit_code, cycles, instret, traps, cnt_redirect, cnt_loaduse,
                 cnt_flush, cnt_ifwait, cnt_hold,
                 ic_access, ic_miss, dc_access, dc_miss, dc_writeback,
                 cnt_branch, cnt_branch_mis, cnt_jump, cnt_jump_mis,
                 ic_pf_issued, ic_pf_useful, ic_pf_late,
                 dc_pf_issued, dc_pf_useful, dc_pf_late,
                 dc_evict, dc_mem_read, dc_mem_write, dc_wbuf_full);
        $display("STATS2 ic_wait=%0d dc_wait=%0d ic_vb_hit=%0d dc_vb_hit=%0d ic_inval=%0d dc_inval=%0d l2_access=%0d l2_miss=%0d l2_writeback=%0d l2_evict=%0d l2_mem_read=%0d l2_mem_write=%0d l2_wait=%0d ic_arb_wait=%0d dc_arb_wait=%0d dc_store_miss=%0d l2_store_miss=%0d ic_evict=%0d ic_mem_read=%0d",
                 ic_wait, dc_wait, ic_vb_hit, dc_vb_hit, ic_inval, dc_inval,
                 l2_access, l2_miss, l2_writeback, l2_evict, l2_mem_read, l2_mem_write,
                 l2_wait, ic_arb_wait, dc_arb_wait, dc_store_miss, l2_store_miss,
                 ic_evict, ic_mem_read);
        // The numbers above are as they stood before this clock edge.
        #1;
        settle_memory();
        if ($value$plusargs("memdump=%s", dump_file)) $writememh(dump_file, u_mem.ram);
        if (g_hier.inclusion_errors() != 0)
            $display("ERROR inclusion: at the end of the run %0d lines of the first-level caches are not in the L2",
                     g_hier.inclusion_errors());
        $display("FINAL memhash=%0d ic_access=%0d ic_miss=%0d ic_pf_issued=%0d ic_pf_useful=%0d dc_access=%0d dc_miss=%0d dc_writeback=%0d dc_evict=%0d dc_pf_issued=%0d dc_pf_useful=%0d dc_mem_read=%0d dc_mem_write=%0d ic_evict=%0d ic_mem_read=%0d ic_vb_hit=%0d ic_inval=%0d dc_vb_hit=%0d dc_inval=%0d l2_access=%0d l2_miss=%0d l2_writeback=%0d l2_evict=%0d l2_mem_read=%0d l2_mem_write=%0d dc_store_miss=%0d l2_store_miss=%0d",
                 memory_hash(), ic_access, ic_miss, ic_pf_issued, ic_pf_useful,
                 dc_access, dc_miss, dc_writeback, dc_evict, dc_pf_issued, dc_pf_useful,
                 dc_mem_read, dc_mem_write, ic_evict, ic_mem_read, ic_vb_hit, ic_inval,
                 dc_vb_hit, dc_inval,
                 l2_access, l2_miss, l2_writeback, l2_evict, l2_mem_read, l2_mem_write,
                 dc_store_miss, l2_store_miss);
        if (trace_fd != 0) $fclose(trace_fd);
        $finish;
    endtask

    initial begin
        if (!$value$plusargs("hex=%s", hex_file)) begin
            $display("core_tb: missing +hex=<program.hex>");
            $finish;
        end
        if (!$value$plusargs("max_cycles=%d", max_cycles)) max_cycles = 5000000;
        if (!$value$plusargs("max_events=%d", max_events)) max_events = 0;
        if ($value$plusargs("trace=%s", trace_file)) trace_fd = $fopen(trace_file, "w");
        if ($value$plusargs("icache_log=%s", log_file))
            u_memsys.u_icache.log_fd = $fopen(log_file, "w");
        if ($value$plusargs("dcache_log=%s", log_file))
            u_memsys.u_dcache.log_fd = $fopen(log_file, "w");
        if ($value$plusargs("vcd=%s", vcd_file)) begin
            $dumpfile(vcd_file);
            $dumpvars(0, core_tb);
        end
        #1 $readmemh(hex_file, u_mem.ram);      // after the RAM has been zeroed
        repeat (2) @(posedge clk);
        rst <= 1'b0;
    end

    always @(posedge clk) begin
        if (!rst) begin
            cycles = cycles + 1;
            if (commit_valid && commit_trap) begin
                traps = traps + 1;
                if (trace_fd != 0)
                    $fwrite(trace_fd, "T %h %h cause=%0d tval=%h\n",
                            commit_pc, commit_instr, commit_cause, commit_tval);
            end else if (commit_valid) begin
                instret = instret + 1;
                if (trace_fd != 0) begin
                    $fwrite(trace_fd, "C %h %h", commit_pc, commit_instr);
                    if (commit_rd_we)
                        $fwrite(trace_fd, " x%0d=%h", commit_rd, commit_rd_data);
                    if (commit_mem_we)
                        $fwrite(trace_fd, " m[%h,%0d]=%h", commit_mem_addr,
                                4'd1 << commit_mem_size, commit_mem_data);
                    $fwrite(trace_fd, "\n");
                end
                if (commit_mem_we && commit_mem_addr == MMIO_CONSOLE)
                    $write("%c", commit_mem_data[7:0]);
                if (commit_mem_we && commit_mem_addr == MMIO_HALT) begin
                    halted = 1'b1;
                    finish_run("halt", commit_mem_data);
                end
            end
            // The second instruction of the cycle follows the first in program
            // order.
            if (commit2_valid && !halted) begin
                instret = instret + 1;
                if (trace_fd != 0) begin
                    $fwrite(trace_fd, "C %h %h", commit2_pc, commit2_instr);
                    if (commit2_rd_we)
                        $fwrite(trace_fd, " x%0d=%h", commit2_rd, commit2_rd_data);
                    $fwrite(trace_fd, "\n");
                end
            end
            if (!halted) begin
                if (cycles >= max_cycles) finish_run("timeout", 64'd0);
                if (max_events != 0 && instret + traps > max_events) finish_run("runaway", 64'd0);
            end
        end
    end
endmodule
