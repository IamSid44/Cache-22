// Unit testbench for the cache hierarchy: rtl/common/mem_subsystem.sv with two
// first-level caches, the arbiter, and the second-level cache below them.

`timescale 1ns / 1ps

module hier_tb;
    parameter int LINE_BYTES = 32;
    parameter int IC_ENABLE  = 1;
    parameter int IC_SETS    = 4;
    parameter int IC_WAYS    = 2;
    parameter int DC_ENABLE  = 1;
    parameter int DC_SETS    = 4;
    parameter int DC_WAYS    = 2;
    parameter int IC_REPL         = 0;
    parameter int DC_REPL         = 0;
    parameter int DC_WRITE_HIT    = 0;
    parameter int DC_WRITE_MISS   = 0;
    parameter int DC_WBUF_ENTRIES = 0;
    parameter int DC_WBUF_MERGE   = 0;
    parameter int IC_VICTIM_ENTRIES = 0;
    parameter int DC_VICTIM_ENTRIES = 0;
    parameter int L2_ENABLE       = 1;
    parameter int L2_SETS         = 8;
    parameter int L2_WAYS         = 2;
    parameter int L2_REPL         = 0;
    parameter int L2_WRITE_HIT    = 0;
    parameter int L2_WRITE_MISS   = 0;
    parameter int L2_WBUF_ENTRIES = 0;
    parameter int L2_WBUF_MERGE   = 0;
    parameter int L2_HIT_LATENCY  = 2;
    parameter int L2_HIT_JITTER   = 0;
    parameter int L2_INCLUSION    = 0;
    parameter int IC_PF_MODE  = 0;
    parameter int DC_PF_MODE  = 0;
    parameter int REGION     = 4096;        // bytes of memory exercised
    parameter int OPS        = 12000;       // accesses on each port

    localparam int LINE_BITS = 8 * LINE_BYTES;
    localparam int HALF      = REGION / 2;

    logic clk = 1'b0;
    logic rst = 1'b1;
    always #5 clk = ~clk;

    logic        imem_req = 1'b0, imem_ready, imem_fault, imem_valid2;
    logic [63:0] imem_addr = 64'b0;
    logic [31:0] imem_rdata, imem_rdata2;
    logic        dmem_re = 1'b0, dmem_we = 1'b0, dmem_ready, dmem_fault;
    logic [63:0] dmem_addr = 64'b0, dmem_wdata = 64'b0, dmem_rdata, dmem_pc = 64'b0;
    logic [1:0]  dmem_size = 2'd0;

    logic                  ma_req, ma_resp;
    logic [63:0]           ma_addr;
    logic [LINE_BITS-1:0]  ma_rdata;
    logic                  mb_req, mb_we, mb_resp;
    logic [63:0]           mb_addr;
    logic [LINE_BITS-1:0]  mb_wdata, mb_rdata;
    logic [LINE_BYTES-1:0] mb_wstrb;

    logic [63:0] ic_access, ic_miss, dc_access, dc_miss, dc_writeback;
    logic [63:0] ic_pf_issued, ic_pf_useful, ic_pf_late;
    logic [63:0] dc_pf_issued, dc_pf_useful, dc_pf_late;
    logic [63:0] dc_evict, dc_mem_read, dc_mem_write, dc_wbuf_full;
    logic [63:0] ic_wait, dc_wait, ic_vb_hit, dc_vb_hit, ic_inval, dc_inval;
    logic [63:0] ic_evict, ic_mem_read;
    logic [63:0] l2_access, l2_miss, l2_writeback, l2_evict, l2_mem_read, l2_mem_write;
    logic [63:0] l2_wait, ic_arb_wait, dc_arb_wait, l2_store_miss, dc_store_miss;

    mem_subsystem #(
        .LINE_BYTES (LINE_BYTES),
        .IC_ENABLE  (IC_ENABLE), .IC_SETS (IC_SETS), .IC_WAYS (IC_WAYS),
        .DC_ENABLE  (DC_ENABLE), .DC_SETS (DC_SETS), .DC_WAYS (DC_WAYS),
        .IC_REPL (IC_REPL), .DC_REPL (DC_REPL),
        .DC_WRITE_HIT (DC_WRITE_HIT), .DC_WRITE_MISS (DC_WRITE_MISS),
        .DC_WBUF_ENTRIES (DC_WBUF_ENTRIES), .DC_WBUF_MERGE (DC_WBUF_MERGE),
        .IC_VICTIM_ENTRIES (IC_VICTIM_ENTRIES), .DC_VICTIM_ENTRIES (DC_VICTIM_ENTRIES),
        .L2_ENABLE (L2_ENABLE), .L2_SETS (L2_SETS), .L2_WAYS (L2_WAYS), .L2_REPL (L2_REPL),
        .L2_WRITE_HIT (L2_WRITE_HIT), .L2_WRITE_MISS (L2_WRITE_MISS),
        .L2_WBUF_ENTRIES (L2_WBUF_ENTRIES), .L2_WBUF_MERGE (L2_WBUF_MERGE),
        .L2_HIT_LATENCY (L2_HIT_LATENCY), .L2_HIT_JITTER (L2_HIT_JITTER),
        .L2_INCLUSION (L2_INCLUSION),
        .IC_PF_MODE (IC_PF_MODE), .DC_PF_MODE (DC_PF_MODE), .PF_DISTANCE (1)
    ) dut (
        .clk (clk), .rst (rst),
        .imem_req (imem_req), .imem_addr (imem_addr), .imem_rdata (imem_rdata),
        .imem_ready (imem_ready), .imem_fault (imem_fault),
        .imem_rdata2 (imem_rdata2), .imem_valid2 (imem_valid2),
        .dmem_re (dmem_re), .dmem_we (dmem_we), .dmem_addr (dmem_addr), .dmem_pc (dmem_pc),
        .dmem_size (dmem_size), .dmem_wdata (dmem_wdata), .dmem_rdata (dmem_rdata),
        .dmem_ready (dmem_ready), .dmem_fault (dmem_fault),
        .ma_req (ma_req), .ma_addr (ma_addr), .ma_resp (ma_resp), .ma_rdata (ma_rdata),
        .mb_req (mb_req), .mb_we (mb_we), .mb_addr (mb_addr), .mb_wdata (mb_wdata),
        .mb_wstrb (mb_wstrb), .mb_resp (mb_resp), .mb_rdata (mb_rdata),
        .ic_access (ic_access), .ic_miss (ic_miss), .dc_access (dc_access),
        .dc_miss (dc_miss), .dc_writeback (dc_writeback),
        .ic_pf_issued (ic_pf_issued), .ic_pf_useful (ic_pf_useful), .ic_pf_late (ic_pf_late),
        .dc_pf_issued (dc_pf_issued), .dc_pf_useful (dc_pf_useful), .dc_pf_late (dc_pf_late),
        .dc_evict (dc_evict), .dc_mem_read (dc_mem_read), .dc_mem_write (dc_mem_write),
        .dc_wbuf_full (dc_wbuf_full),
        .ic_wait (ic_wait), .dc_wait (dc_wait), .ic_vb_hit (ic_vb_hit), .dc_vb_hit (dc_vb_hit),
        .ic_inval (ic_inval), .dc_inval (dc_inval), .ic_evict (ic_evict),
        .ic_mem_read (ic_mem_read),
        .l2_access (l2_access), .l2_miss (l2_miss), .l2_writeback (l2_writeback),
        .l2_store_miss (l2_store_miss), .dc_store_miss (dc_store_miss),
        .l2_evict (l2_evict), .l2_mem_read (l2_mem_read), .l2_mem_write (l2_mem_write),
        .l2_wait (l2_wait), .ic_arb_wait (ic_arb_wait), .dc_arb_wait (dc_arb_wait)
    );

    sim_memory #(
        .LINE_BYTES (LINE_BYTES)
    ) mem (
        .clk (clk), .rst (rst),
        .a_req (ma_req), .a_addr (ma_addr), .a_resp (ma_resp), .a_rdata (ma_rdata),
        .b_req (mb_req), .b_we (mb_we), .b_addr (mb_addr),
        .b_wdata (mb_wdata), .b_wstrb (mb_wstrb),
        .b_resp (mb_resp), .b_rdata (mb_rdata)
    );

    logic [7:0] ref_mem [0:REGION-1];       // what memory should contain

    integer i_seed, d_seed, errors, abandoned;
    integer d_waited;                       // cycles the last data access waited
    logic   go = 1'b0;                      // the random phase has begun
    logic   i_done = 1'b0, d_done = 1'b0;

    function automatic logic [63:0] expected(input logic [63:0] a, input integer bytes);
        logic [63:0] value;
        value = 64'b0;
        for (int b = 0; b < bytes; b++) value[8*b +: 8] = ref_mem[a + b];
        return value;
    endfunction

    function automatic logic [63:0] low_bytes(input logic [63:0] value, input logic [1:0] sz);
        case (sz)
            2'd0:    return value & 64'hFF;
            2'd1:    return value & 64'hFFFF;
            2'd2:    return value & 64'hFFFF_FFFF;
            default: return value;
        endcase
    endfunction

    task automatic complain(input string what, input logic [63:0] a,
                            input logic [63:0] got, input logic [63:0] want);
        errors = errors + 1;
        if (errors <= 5) $display("mismatch: %s at %h gave %h, expected %h", what, a, got, want);
    endtask

    // ---- instruction side: fetches from the lower half ----
    task automatic fetch(input logic [63:0] a);
        logic        completed;
        integer      waited;
        logic [63:0] want, want2;
        want  = expected(a, 4);
        want2 = expected(a + 4, 4);
        @(negedge clk);
        imem_req = 1'b1; imem_addr = a;
        completed = 1'b0;
        waited    = 0;
        while (!completed) begin
            @(posedge clk);
            if (imem_ready) begin
                completed = 1'b1;
                if (imem_rdata !== want[31:0])
                    complain("fetch", a, imem_rdata, want);
                if (imem_valid2 && imem_rdata2 !== want2[31:0])
                    complain("second fetch", a + 4, imem_rdata2, want2);
            end else begin
                waited = waited + 1;
                if (waited > 5000) begin
                    $display("FAIL fetch at %h never completed", a);
                    $finish;
                end
                // A redirect: give up on this one.
                if ({$random(i_seed)} % 8 == 0) begin
                    abandoned = abandoned + 1;
                    completed = 1'b1;
                end
            end
        end
        @(negedge clk);
        imem_req = 1'b0;
    endtask

    initial begin : i_side
        logic [63:0] a;
        wait (go);
        for (int op = 0; op < OPS; op++) begin
            // Mostly a loop of a few lines, sometimes a jump anywhere.
            if ({$random(i_seed)} % 5 == 0) a = {$random(i_seed)} % HALF;
            else                            a = {$random(i_seed)} % (6 * LINE_BYTES);
            fetch((a >> 2) << 2);
            if ({$random(i_seed)} % 6 == 0) repeat ({$random(i_seed)} % 3 + 1) @(posedge clk);
        end
        i_done = 1'b1;
    end

    // ---- data side: loads from anywhere, stores to the upper half ----
    task automatic data(input logic w, input logic [63:0] a, input logic [1:0] sz,
                        input logic [63:0] d);
        logic   completed;
        integer waited;
        @(negedge clk);
        dmem_re = !w; dmem_we = w; dmem_addr = a; dmem_size = sz; dmem_wdata = d;
        dmem_pc = 64'h100 + 4 * ({$random(d_seed)} % 4);
        completed = 1'b0;
        waited    = 0;
        while (!completed) begin
            @(posedge clk);
            if (dmem_ready) begin
                completed = 1'b1;
                if (w) begin
                    for (int b = 0; b < (1 << sz); b++) ref_mem[a + b] = d[8*b +: 8];
                end else if (low_bytes(dmem_rdata, sz) !== expected(a, 1 << sz)) begin
                    complain("load", a, low_bytes(dmem_rdata, sz), expected(a, 1 << sz));
                end
                d_waited = waited;
            end else begin
                waited = waited + 1;
                if (waited > 5000) begin
                    $display("FAIL data access at %h never completed", a);
                    $finish;
                end
            end
        end
        @(negedge clk);
        dmem_re = 1'b0; dmem_we = 1'b0;
    endtask

    initial begin : d_side
        logic        w;
        logic [1:0]  sz;
        logic [63:0] a, d;
        wait (go);
        for (int op = 0; op < OPS; op++) begin
            w  = ({$random(d_seed)} % 3 == 0);
            sz = {$random(d_seed)} % 4;
            if (w) begin
                // Mostly a hot area of a few lines, sometimes anywhere in the half.
                if ({$random(d_seed)} % 4 == 0) a = HALF + {$random(d_seed)} % HALF;
                else                            a = HALF + {$random(d_seed)} % (8 * LINE_BYTES);
            end else begin
                case ({$random(d_seed)} % 8)
                    0:       a = {$random(d_seed)} % REGION;
                    1, 2:    a = {$random(d_seed)} % (6 * LINE_BYTES);     // where the fetches are
                    default: a = HALF + {$random(d_seed)} % (8 * LINE_BYTES);
                endcase
            end
            a = (a >> sz) << sz;
            d = {$random(d_seed), $random(d_seed)};
            data(w, a, sz, d);
            if ({$random(d_seed)} % 4 == 0) repeat ({$random(d_seed)} % 3 + 1) @(posedge clk);
        end
        d_done = 1'b1;
    end

    // ---- what the caches still hold, laid over memory ----
    logic [63:0]           settle_base;
    logic [LINE_BITS-1:0]  settle_line;
    logic [LINE_BYTES-1:0] settle_strb;
    integer                settle_slot;

    task automatic settle_put();
        for (int b = 0; b < LINE_BYTES; b++)
            if (settle_strb[b]) mem.ram[settle_base[15:0] + b] = settle_line[8*b +: 8];
    endtask

    `define SETTLE_CACHE(C, LINES_N, VB_N) \
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

    // The L2 only exists when it is enabled.
    string log_file;
    generate
        if (L2_ENABLE != 0) begin : g_hier
            wire at_rest = dut.g_l2.u_l2.sim_at_rest && !dut.g_l2.u_arb.serve;
            task automatic settle();
                `SETTLE_CACHE(dut.g_l2.u_l2, L2_SETS * L2_WAYS, 0)
            endtask
            function automatic integer inclusion_errors();
                return dut.g_l2.inclusion_errors();
            endfunction
            initial begin
                if ($value$plusargs("l2_log=%s", log_file))
                    dut.g_l2.u_l2.log_fd = $fopen(log_file, "w");
            end
        end else begin : g_hier
            wire at_rest = 1'b1;
            task automatic settle();
            endtask
            function automatic integer inclusion_errors();
                return 0;
            endfunction
        end
    endgenerate

    wire at_rest = g_hier.at_rest && dut.u_icache.sim_at_rest && dut.u_dcache.sim_at_rest;

    string  ic_log, dc_log;
    integer seed, waited;

    initial begin
        if (!$value$plusargs("seed=%d", seed)) seed = 1;
        if ($value$plusargs("ic_log=%s", ic_log)) dut.u_icache.log_fd = $fopen(ic_log, "w");
        if ($value$plusargs("dc_log=%s", dc_log)) dut.u_dcache.log_fd = $fopen(dc_log, "w");
        i_seed = seed + 100;
        d_seed = seed + 200;
        errors = 0;
        abandoned = 0;
        #1;
        for (int i = 0; i < REGION; i++) begin
            ref_mem[i] = $random(seed);
            mem.ram[i] = ref_mem[i];
        end
        repeat (3) @(posedge clk);
        @(negedge clk) rst = 1'b0;

        // How long a miss takes.
        if (IC_ENABLE != 0 && DC_ENABLE != 0 && L2_ENABLE != 0 && DC_SETS == 4 &&
            DC_WAYS == 2 && L2_SETS == 8 && L2_WAYS == 2 && DC_REPL <= 1 &&
            DC_PF_MODE == 0 && DC_VICTIM_ENTRIES == 0 && L2_HIT_JITTER == 0 &&
            mem.jitter == 0 && REGION >= 4096) begin
            // In neither cache: the L2's latency, memory's, and two cycles in
            // each cache.
            data(1'b0, 64 * LINE_BYTES, 2'd3, 64'b0);
            if (d_waited != L2_HIT_LATENCY + mem.latency + 4) begin
                errors = errors + 1;
                $display("timing: a miss in both caches waited %0d cycles, expected %0d",
                         d_waited, L2_HIT_LATENCY + mem.latency + 4);
            end
            data(1'b0, 68 * LINE_BYTES, 2'd3, 64'b0);
            data(1'b0, 76 * LINE_BYTES, 2'd3, 64'b0);   // line 64 has now left the data cache
            // In the L2 only: its latency and two cycles in the data cache.
            data(1'b0, 64 * LINE_BYTES, 2'd3, 64'b0);
            if (d_waited != L2_HIT_LATENCY + 2) begin
                errors = errors + 1;
                $display("timing: a miss served by the L2 waited %0d cycles, expected %0d",
                         d_waited, L2_HIT_LATENCY + 2);
            end
            // In the data cache: no wait at all.
            data(1'b0, 64 * LINE_BYTES + 8, 2'd3, 64'b0);
            if (d_waited != 0) begin
                errors = errors + 1;
                $display("timing: a hit waited %0d cycles", d_waited);
            end
        end

        go = 1'b1;
        wait (i_done && d_done);

        // Read the upper half back through the data cache: nothing stored may
        // have been lost on its way through the levels.
        for (int a = HALF; a < REGION; a += 8) data(1'b0, a, 2'd3, 64'b0);

        // Let every line and every write that is on its way arrive.
        waited = 0;
        @(negedge clk);
        while (!at_rest) begin
            @(negedge clk);
            waited = waited + 1;
            if (waited > 20000) begin
                $display("FAIL the hierarchy never came to rest");
                $finish;
            end
        end

        // Memory itself, with what the caches hold laid over it: the L2 first,
        // then the data cache, whose copy of a line is the newer.
        g_hier.settle();
        if (DC_ENABLE != 0) begin
            `SETTLE_CACHE(dut.u_dcache, DC_SETS * DC_WAYS, DC_VICTIM_ENTRIES)
        end
        for (int a = 0; a < REGION; a++) begin
            if (mem.ram[a] !== ref_mem[a]) begin
                errors = errors + 1;
                if (errors <= 5)
                    $display("memory: byte %h is %h, expected %h", a, mem.ram[a], ref_mem[a]);
            end
        end
        if (g_hier.inclusion_errors() != 0) begin
            errors = errors + 1;
            $display("inclusion: %0d lines of the first-level caches are not in the L2",
                     g_hier.inclusion_errors());
        end

        // The counters, for the regression to compare with the models'.
        $display("COUNT_IC access=%0d miss=%0d evict=%0d pf_issued=%0d pf_useful=%0d mem_read=%0d vb_hit=%0d inval=%0d",
                 ic_access, ic_miss, ic_evict, ic_pf_issued, ic_pf_useful, ic_mem_read,
                 ic_vb_hit, ic_inval);
        $display("COUNT_DC access=%0d miss=%0d writeback=%0d evict=%0d pf_issued=%0d pf_useful=%0d mem_read=%0d mem_write=%0d vb_hit=%0d inval=%0d store_miss=%0d",
                 dc_access, dc_miss, dc_writeback, dc_evict, dc_pf_issued, dc_pf_useful,
                 dc_mem_read, dc_mem_write, dc_vb_hit, dc_inval, dc_store_miss);
        $display("COUNT_L2 access=%0d miss=%0d writeback=%0d evict=%0d mem_read=%0d mem_write=%0d store_miss=%0d",
                 l2_access, l2_miss, l2_writeback, l2_evict, l2_mem_read, l2_mem_write,
                 l2_store_miss);
        if (errors == 0)
            $display("PASS %0d fetches (%0d abandoned), %0d data accesses, %0d L2 accesses, %0d L2 misses, %0d lines taken back, %0d from victim buffers",
                     ic_access, abandoned, dc_access, l2_access, l2_miss,
                     ic_inval + dc_inval, ic_vb_hit + dc_vb_hit);
        else
            $display("FAIL %0d mismatches", errors);
        $finish;
    end
endmodule
