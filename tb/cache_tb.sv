// Unit testbench for rtl/common/cache.sv.

`timescale 1ns / 1ps

module cache_tb;
    parameter int ENABLE     = 1;
    parameter int SETS       = 8;
    parameter int WAYS       = 1;
    parameter int LINE_BYTES = 32;
    parameter int REPL         = 0;
    parameter int WRITE_HIT    = 0;
    parameter int WRITE_MISS   = 0;
    parameter int WBUF_ENTRIES = 0;
    parameter int WBUF_MERGE   = 0;
    parameter int VICTIM_ENTRIES = 0;
    parameter int INCLUSION    = 0;         // the cache announces its evictions (rc_req)
    parameter int GIVE_UP      = 1;         // random requests to give lines up
    parameter int REGION     = 4096;        // bytes of memory exercised
    parameter int OPS        = 20000;

    localparam int LINE_BITS = 8 * LINE_BYTES;
    localparam int SET_SPAN  = SETS * LINE_BYTES;   // distance between lines of one set
    localparam int OFF_BITS  = $clog2(LINE_BYTES);
    localparam int IDX_BITS  = $clog2(SETS);

    logic clk = 1'b0;
    logic rst = 1'b1;
    always #5 clk = ~clk;

    logic        req = 1'b0, we = 1'b0, ready;
    logic [63:0] addr = 64'b0, wdata = 64'b0, rdata;
    logic [1:0]  size = 2'd0;

    logic                  mem_req, mem_we, mem_resp;
    logic [63:0]           mem_addr;
    logic [LINE_BITS-1:0]  mem_wdata, mem_rdata;
    logic [LINE_BYTES-1:0] mem_wstrb;
    logic [63:0]           cnt_access, cnt_miss, cnt_writeback;
    logic [63:0]           cnt_pf_issued, cnt_pf_useful, cnt_pf_late;
    logic [63:0]           cnt_evict, cnt_mem_read, cnt_mem_write, cnt_wbuf_full;
    logic [63:0]           cnt_wait, cnt_vb_hit, cnt_inval, cnt_store_miss;
    logic                  inv_req = 1'b0, inv_dirty;
    logic [63:0]           inv_addr = 64'b0;
    logic [LINE_BITS-1:0]  inv_line;
    logic                  inv_random = 1'b0;   // random give-up requests on
    logic                  rc_req;
    logic [63:0]           rc_addr;
    logic                  pf_req = 1'b0, pf_done;
    logic [63:0]           pf_addr = 64'b0;
    logic                  pf_random = 1'b0;    // random prefetch requests on

    cache #(
        .ENABLE     (ENABLE),
        .SETS       (SETS),
        .WAYS       (WAYS),
        .LINE_BYTES (LINE_BYTES),
        .REPL         (REPL),
        .WRITE_HIT    (WRITE_HIT),
        .WRITE_MISS   (WRITE_MISS),
        .WBUF_ENTRIES (WBUF_ENTRIES),
        .WBUF_MERGE   (WBUF_MERGE),
        .VICTIM_ENTRIES (VICTIM_ENTRIES),
        .INCLUSION    (INCLUSION)
    ) dut (
        .clk (clk), .rst (rst),
        .req (req), .we (we), .addr (addr), .size (size), .wdata (wdata),
        .ready (ready), .rdata (rdata),
        .lwdata ({LINE_BITS{1'b0}}), .lwstrb ({LINE_BYTES{1'b0}}), .lrdata (),
        .pf_req (pf_req), .pf_addr (pf_addr), .pf_done (pf_done),
        .mem_req (mem_req), .mem_we (mem_we), .mem_addr (mem_addr),
        .mem_wdata (mem_wdata), .mem_wstrb (mem_wstrb),
        .mem_resp (mem_resp), .mem_rdata (mem_rdata),
        .inv_req (inv_req), .inv_addr (inv_addr),
        .inv_dirty (inv_dirty), .inv_line (inv_line),
        .rc_req (rc_req), .rc_addr (rc_addr),
        .rc_dirty (1'b0), .rc_line ({LINE_BITS{1'b0}}),
        .cnt_access (cnt_access), .cnt_miss (cnt_miss), .cnt_writeback (cnt_writeback),
        .cnt_pf_issued (cnt_pf_issued), .cnt_pf_useful (cnt_pf_useful),
        .cnt_pf_late (cnt_pf_late),
        .cnt_evict (cnt_evict), .cnt_mem_read (cnt_mem_read),
        .cnt_mem_write (cnt_mem_write), .cnt_wbuf_full (cnt_wbuf_full),
        .cnt_wait (cnt_wait), .cnt_vb_hit (cnt_vb_hit), .cnt_inval (cnt_inval),
        .cnt_store_miss (cnt_store_miss)
    );

    // The cache below, as far as this testbench plays it: now and then it asks
    // for a line to be given up, for one cycle.
    integer inv_seed;
    logic [63:0] inv_base;
    always @(negedge clk) begin
        inv_req = 1'b0;
        if (inv_random && ({$random(inv_seed)} % 6 == 0)) begin
            inv_req = 1'b1;
            // Mostly the lines the test is busy with, so that it often has them.
            if ({$random(inv_seed)} % 3 == 0) inv_addr = {$random(inv_seed)} % REGION;
            else                              inv_addr = {$random(inv_seed)} % (8 * LINE_BYTES);
        end
    end
    always @(posedge clk) begin
        if (inv_req && inv_dirty) begin
            inv_base = (inv_addr >> OFF_BITS) << OFF_BITS;
            for (int b = 0; b < LINE_BYTES; b++) mem.ram[inv_base + b] = inv_line[8*b +: 8];
        end
    end

    // Random prefetch requests: a new one now and then, held until the cache
    // says it has dealt with it.
    integer pf_seed;
    always @(negedge clk) begin
        if (pf_random) begin
            if (pf_req && pf_done) pf_req = 1'b0;
            if (!pf_req && ({$random(pf_seed)} % 3 == 0)) begin
                pf_req  = 1'b1;
                pf_addr = {$random(pf_seed)} % REGION;
            end
        end
    end

    // One prefetch request, waited for.
    task automatic prefetch(input logic [63:0] a);
        @(negedge clk);
        pf_req = 1'b1; pf_addr = a;
        @(posedge clk);
        while (!pf_done) @(posedge clk);
        @(negedge clk);
        pf_req = 1'b0;
    endtask

    sim_memory #(
        .LINE_BYTES (LINE_BYTES)
    ) mem (
        .clk (clk), .rst (rst),
        .a_req (1'b0), .a_addr (64'b0), .a_resp (), .a_rdata (),
        .b_req (mem_req), .b_we (mem_we), .b_addr (mem_addr),
        .b_wdata (mem_wdata), .b_wstrb (mem_wstrb),
        .b_resp (mem_resp), .b_rdata (mem_rdata)
    );

    logic [7:0] ref_mem [0:REGION-1];       // what memory should contain

    integer seed, errors, abandoned, waited;
    logic   completed;
    logic [63:0]          line_base;
    logic [LINE_BITS-1:0] line_data;

    function automatic logic [63:0] expected(input logic [63:0] a, input logic [1:0] sz);
        logic [63:0] value;
        value = 64'b0;
        for (int b = 0; b < (1 << sz); b++) value[8*b +: 8] = ref_mem[a + b];
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

    // One access.
    task automatic access(input logic w, input logic [63:0] a, input logic [1:0] sz,
                          input logic [63:0] d, input logic may_abandon);
        @(negedge clk);
        req = 1'b1; we = w; addr = a; size = sz; wdata = d;
        completed = 1'b0;
        waited    = 0;
        while (!completed) begin
            @(posedge clk);
            if (ready) begin
                completed = 1'b1;
                if (w) begin
                    for (int b = 0; b < (1 << sz); b++) ref_mem[a + b] = d[8*b +: 8];
                end else if (low_bytes(rdata, sz) !== expected(a, sz)) begin
                    errors = errors + 1;
                    if (errors <= 5)
                        $display("mismatch: read %0d bytes at %h gave %h, expected %h",
                                 1 << sz, a, low_bytes(rdata, sz), expected(a, sz));
                end
            end else begin
                waited = waited + 1;
                if (waited > 2000) begin
                    $display("FAIL request at %h never completed", a);
                    $finish;
                end
                if (!w && may_abandon && ({$random(seed)} % 6 == 0)) begin
                    abandoned = abandoned + 1;
                    completed = 1'b1;
                end
            end
        end
        @(negedge clk);
        req = 1'b0;
    endtask

    // LRU and FIFO replacement rest on one invariant: within a set, the ages
    // are always the numbers 0 ..
    task automatic check_ages(input string when_checked);
        logic [31:0] seen, age;
        logic        bad;
        @(negedge clk);
        if (ENABLE != 0 && REPL <= 1) begin
            bad = 1'b0;
            for (int s = 0; s < SETS; s++) begin
                seen = 32'b0;
                for (int k = 0; k < WAYS; k++) begin
                    age = dut.age[s * WAYS + k];
                    if (age >= WAYS || seen[age]) bad = 1'b1;
                    else seen[age] = 1'b1;
                end
            end
            if (bad) begin
                errors = errors + 1;
                $display("ages are not a permutation within a set (%s)", when_checked);
            end
        end
    endtask

    string       log_file;
    integer      op;
    logic [1:0]  op_size;
    logic [63:0] op_addr, op_data;

    initial begin
        if (!$value$plusargs("seed=%d", seed)) seed = 1;
        if ($value$plusargs("log=%s", log_file)) dut.log_fd = $fopen(log_file, "w");
        pf_seed = seed + 1000;
        inv_seed = seed + 2000;
        errors = 0;
        abandoned = 0;
        #1;
        for (int i = 0; i < REGION; i++) begin
            ref_mem[i] = $random(seed);
            mem.ram[i] = ref_mem[i];
        end
        // Real memory cells power up holding anything.
        for (int e = 0; e < SETS * WAYS; e++) begin
            dut.tags[e]  = e % 4;
            dut.lines[e] = {LINE_BYTES{8'hA5}};
        end
        repeat (3) @(posedge clk);
        @(negedge clk) rst = 1'b0;

        // Replacement order.
        if (ENABLE != 0 && WAYS > 1 && REPL == 0) begin
            for (int k = 0; k < WAYS; k++) access(1'b0, k * SET_SPAN, 2'd3, 64'b0, 1'b0);
            access(1'b0, 0, 2'd3, 64'b0, 1'b0);
            access(1'b0, WAYS * SET_SPAN, 2'd3, 64'b0, 1'b0);
            access(1'b0, 0, 2'd3, 64'b0, 1'b0);
            if (waited != 0) begin
                errors = errors + 1;
                $display("replacement: the most recently used line was evicted");
            end
            access(1'b0, SET_SPAN, 2'd3, 64'b0, 1'b0);
            if (waited == 0) begin
                errors = errors + 1;
                $display("replacement: the least recently used line was kept");
            end
        end

        // Prefetching.
        if (ENABLE != 0) begin
            logic [63:0] issued_before, useful_before;
            issued_before = cnt_pf_issued;
            useful_before = cnt_pf_useful;
            prefetch(REGION - LINE_BYTES);
            repeat (40) @(posedge clk);             // longer than any memory latency
            access(1'b0, REGION - LINE_BYTES, 2'd3, 64'b0, 1'b0);
            if (waited != 0) begin
                errors = errors + 1;
                $display("prefetch: the prefetched line was not there");
            end
            access(1'b0, REGION - LINE_BYTES + 8, 2'd3, 64'b0, 1'b0);
            if (cnt_pf_issued != issued_before + 1 || cnt_pf_useful != useful_before + 1) begin
                errors = errors + 1;
                $display("prefetch: issued %0d, useful %0d, expected 1 and 1",
                         cnt_pf_issued - issued_before, cnt_pf_useful - useful_before);
            end
            prefetch(REGION - LINE_BYTES + 4);      // same line: already present
            repeat (3) @(posedge clk);
            if (cnt_pf_issued != issued_before + 1) begin
                errors = errors + 1;
                $display("prefetch: a line already present was fetched again");
            end
        end

        // A line that is already in the cache must be served at once even while
        // another line is being fetched.
        if (ENABLE != 0 && SETS * WAYS > 1 && (REPL != 2 || SETS > 1)) begin
            access(1'b0, REGION - LINE_BYTES, 2'd3, 64'b0, 1'b0);    // make it most recent
            prefetch(REGION - 2 * LINE_BYTES);                       // a neighbour, not present
            access(1'b0, REGION - LINE_BYTES, 2'd3, 64'b0, 1'b0);
            if (waited != 0) begin
                errors = errors + 1;
                $display("prefetch: a hit had to wait for a line that was on its way");
            end
        end

        check_ages("after the directed checks");
        pf_random = 1'b1;
        inv_random = (GIVE_UP != 0) && (ENABLE != 0);
        for (op = 0; op < OPS; op++) begin
            op_size = {$random(seed)} % 4;
            // Mostly a hot area of a few lines, sometimes anywhere, so that
            // both hits and evictions are frequent.
            if ({$random(seed)} % 4 == 0) op_addr = {$random(seed)} % REGION;
            else                          op_addr = {$random(seed)} % (8 * LINE_BYTES);
            op_addr = (op_addr >> op_size) << op_size;       // align to the size
            op_data = {$random(seed), $random(seed)};
            access(({$random(seed)} % 3 == 0), op_addr, op_size, op_data, 1'b1);
            // Sometimes leave the cache idle for a few cycles.
            if ({$random(seed)} % 4 == 0) repeat ({$random(seed)} % 3 + 1) @(posedge clk);
        end

        // Read the whole region back: nothing written may have been lost.
        for (int a = 0; a < REGION; a += 8) access(1'b0, a, 2'd3, 64'b0, 1'b0);
        check_ages("at the end");

        // Memory itself.
        @(negedge clk);
        pf_random  = 1'b0;
        pf_req     = 1'b0;
        inv_random = 1'b0;
        inv_req    = 1'b0;
        waited     = 0;
        while (ENABLE != 0 && (!dut.idle || dut.wb_count != 0)) begin
            @(negedge clk);
            waited = waited + 1;
            if (waited > 5000) begin
                $display("FAIL the cache never came to rest");
                $finish;
            end
        end
        if (ENABLE != 0) begin
            for (int e = 0; e < SETS * WAYS; e++) begin
                if (dut.valid[e] && dut.dirty[e]) begin
                    if (WRITE_HIT != 0) begin
                        errors = errors + 1;
                        $display("a line is dirty although stores are written through");
                    end
                    line_base = ((dut.tags[e] << IDX_BITS) | (e / WAYS)) << OFF_BITS;
                    line_data = dut.lines[e];
                    for (int b = 0; b < LINE_BYTES; b++)
                        mem.ram[line_base + b] = line_data[8*b +: 8];
                end
            end
            for (int k = 0; k < VICTIM_ENTRIES; k++) begin
                if (dut.vb_valid[k] && dut.vb_dirty[k]) begin
                    line_base = dut.vb_addr[k];
                    line_data = dut.vb_line[k];
                    for (int b = 0; b < LINE_BYTES; b++)
                        mem.ram[line_base + b] = line_data[8*b +: 8];
                end
            end
        end
        for (int a = 0; a < REGION; a++) begin
            if (mem.ram[a] !== ref_mem[a]) begin
                errors = errors + 1;
                if (errors <= 5)
                    $display("memory: byte %h is %h, expected %h", a, mem.ram[a], ref_mem[a]);
            end
        end

        // The counters, for the regression to compare with the model's.
        $display("COUNT access=%0d miss=%0d writeback=%0d evict=%0d pf_issued=%0d pf_useful=%0d mem_read=%0d mem_write=%0d vb_hit=%0d inval=%0d store_miss=%0d",
                 cnt_access, cnt_miss, cnt_writeback, cnt_evict, cnt_pf_issued, cnt_pf_useful,
                 cnt_mem_read, cnt_mem_write, cnt_vb_hit, cnt_inval, cnt_store_miss);
        if (errors == 0)
            $display("PASS %0d accesses, %0d misses, %0d write-backs, %0d prefetches, %0d reads abandoned, %0d memory writes, %0d from the victim buffer, %0d lines given up",
                     cnt_access, cnt_miss, cnt_writeback, cnt_pf_issued, abandoned, cnt_mem_write,
                     cnt_vb_hit, cnt_inval);
        else
            $display("FAIL %0d mismatches in %0d accesses", errors, cnt_access);
        $finish;
    end
endmodule
