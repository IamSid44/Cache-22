// Memory subsystem: everything between the core and main memory.

module mem_subsystem
    import rv_pkg::*;
#(
    parameter int RAM_BYTES  = 65536,   // RAM occupies [0, RAM_BYTES)
    parameter int LINE_BYTES = 32,
    parameter int IC_ENABLE  = 1,
    parameter int IC_SETS    = 32,
    parameter int IC_WAYS    = 1,
    parameter int DC_ENABLE  = 1,
    parameter int DC_SETS    = 32,
    parameter int DC_WAYS    = 1,
    // Policies (rtl/common/cache.sv).
    parameter int IC_REPL         = 0,
    parameter int DC_REPL         = 0,
    parameter int DC_WRITE_HIT    = 0,  // 0 write-back, 1 write-through
    parameter int DC_WRITE_MISS   = 0,  // 0 allocate, 1 no-allocate
    parameter int DC_WBUF_ENTRIES = 0,
    parameter int DC_WBUF_MERGE   = 0,
    // Victim buffers beside the two first-level caches (0 = none).
    parameter int IC_VICTIM_ENTRIES = 0,
    parameter int DC_VICTIM_ENTRIES = 0,
    // The second-level cache.
    parameter int L2_ENABLE       = 0,
    parameter int L2_SETS         = 64,
    parameter int L2_WAYS         = 4,
    parameter int L2_REPL         = 0,
    parameter int L2_WRITE_HIT    = 0,
    parameter int L2_WRITE_MISS   = 0,
    parameter int L2_WBUF_ENTRIES = 0,
    parameter int L2_WBUF_MERGE   = 0,
    parameter int L2_HIT_LATENCY  = 4,  // cycles from a request to the answer on a hit (>= 1)
    parameter int L2_HIT_JITTER   = 0,  // random extra cycles, for verification
    parameter int L2_INCLUSION    = 0,  // 0 non-inclusive, 1 inclusive
    // Prefetchers (rtl/common/prefetcher.sv): 0 off, 1 next line, 2 stride, 3
    // random lines.
    parameter int IC_PF_MODE  = 0,
    parameter int DC_PF_MODE  = 0,
    parameter int PF_DISTANCE = 4
) (
    input  logic                    clk,
    input  logic                    rst,

    // core, instruction port
    input  logic                    imem_req,
    input  logic [63:0]             imem_addr,
    output logic [31:0]             imem_rdata,
    output logic                    imem_ready,
    output logic                    imem_fault,
    // The instruction after it, for a core that fetches two per cycle.
    output logic [31:0]             imem_rdata2,
    output logic                    imem_valid2,

    // core, data port
    input  logic                    dmem_re,
    input  logic                    dmem_we,
    input  logic [63:0]             dmem_addr,
    input  logic [63:0]             dmem_pc,    // the instruction making the access
    input  logic [1:0]              dmem_size,
    input  logic [63:0]             dmem_wdata,
    output logic [63:0]             dmem_rdata,
    output logic                    dmem_ready,
    output logic                    dmem_fault,

    // memory port A (instruction side, read only)
    output logic                    ma_req,
    output logic [63:0]             ma_addr,
    input  logic                    ma_resp,
    input  logic [8*LINE_BYTES-1:0] ma_rdata,

    // memory port B (data side; everything when there is an L2)
    output logic                    mb_req,
    output logic                    mb_we,
    output logic [63:0]             mb_addr,
    output logic [8*LINE_BYTES-1:0] mb_wdata,
    output logic [LINE_BYTES-1:0]   mb_wstrb,
    input  logic                    mb_resp,
    input  logic [8*LINE_BYTES-1:0] mb_rdata,

    // statistics
    output logic [63:0]             ic_access,
    output logic [63:0]             ic_miss,
    output logic [63:0]             dc_access,
    output logic [63:0]             dc_miss,
    output logic [63:0]             dc_writeback,
    output logic [63:0]             ic_pf_issued,
    output logic [63:0]             ic_pf_useful,
    output logic [63:0]             ic_pf_late,
    output logic [63:0]             dc_pf_issued,
    output logic [63:0]             dc_pf_useful,
    output logic [63:0]             dc_pf_late,
    output logic [63:0]             dc_evict,
    output logic [63:0]             dc_mem_read,
    output logic [63:0]             dc_mem_write,
    output logic [63:0]             dc_wbuf_full,
    // ... of the hierarchy
    output logic [63:0]             ic_wait,        // cycles a fetch waited for the I cache
    output logic [63:0]             dc_wait,        // cycles an access waited for the D cache
    output logic [63:0]             ic_vb_hit,      // misses served by a victim buffer
    output logic [63:0]             dc_vb_hit,
    output logic [63:0]             ic_inval,       // lines taken back by an inclusive L2
    output logic [63:0]             dc_inval,
    output logic [63:0]             ic_evict,
    output logic [63:0]             ic_mem_read,
    output logic [63:0]             l2_access,
    output logic [63:0]             l2_miss,
    output logic [63:0]             l2_store_miss,  // ... of which were writes from above
    output logic [63:0]             dc_store_miss,  // data cache misses that were stores
    output logic [63:0]             l2_writeback,
    output logic [63:0]             l2_evict,
    output logic [63:0]             l2_mem_read,
    output logic [63:0]             l2_mem_write,
    output logic [63:0]             l2_wait,        // cycles a request waited for the L2
    output logic [63:0]             ic_arb_wait,    // cycles a cache waited for the other's turn
    output logic [63:0]             dc_arb_wait
);
    localparam int LINE_BITS = 8 * LINE_BYTES;

    wire i_in_ram = (imem_addr < RAM_BYTES);
    wire d_in_ram = (dmem_addr < RAM_BYTES);
    wire d_mmio   = (dmem_addr == MMIO_CONSOLE) || (dmem_addr == MMIO_HALT);

    // The memory side of the two first-level caches, and what an inclusive L2
    // asks them to give up.
    logic                  ic_m_req, ic_m_resp;
    logic [63:0]           ic_m_addr;
    logic [LINE_BITS-1:0]  ic_m_rdata;
    logic                  dc_m_req, dc_m_we, dc_m_resp;
    logic [63:0]           dc_m_addr;
    logic [LINE_BITS-1:0]  dc_m_wdata, dc_m_rdata;
    logic [LINE_BYTES-1:0] dc_m_wstrb;
    logic                  inv_req;
    logic [63:0]           inv_addr;
    logic                  ic_inv_dirty, dc_inv_dirty;
    logic [LINE_BITS-1:0]  ic_inv_line, dc_inv_line;

    // ---- instruction side ----
    logic        ic_ready;
    logic [63:0] ic_rdata;

    // A prefetch request for an address outside the RAM is dropped here.
    logic        ic_pf_req, ic_pf_done;
    logic [63:0] ic_pf_addr;
    wire         ic_pf_ok = (ic_pf_addr < RAM_BYTES);

    prefetcher #(
        .MODE       (IC_PF_MODE),
        .LINE_BYTES (LINE_BYTES),
        .DISTANCE   (PF_DISTANCE)
    ) u_ipf (
        .clk       (clk),
        .rst       (rst),
        .acc_valid (imem_req && i_in_ram && ic_ready),
        .acc_addr  (imem_addr),
        .acc_pc    (imem_addr),
        .pf_req    (ic_pf_req),
        .pf_addr   (ic_pf_addr),
        .pf_done   (ic_pf_done || (ic_pf_req && !ic_pf_ok))
    );

    cache #(
        .ENABLE         (IC_ENABLE),
        .SETS           (IC_SETS),
        .WAYS           (IC_WAYS),
        .LINE_BYTES     (LINE_BYTES),
        .REPL           (IC_REPL),
        .VICTIM_ENTRIES (IC_VICTIM_ENTRIES)
    ) u_icache (
        .clk           (clk),
        .rst           (rst),
        .req           (imem_req && i_in_ram),
        .we            (1'b0),
        .addr          (imem_addr),
        .size          (2'd2),
        .wdata         (64'b0),
        .ready         (ic_ready),
        .rdata         (ic_rdata),
        .lwdata        ({LINE_BITS{1'b0}}),
        .lwstrb        ({LINE_BYTES{1'b0}}),
        .lrdata        (),
        .pf_req        (ic_pf_req && ic_pf_ok),
        .pf_addr       (ic_pf_addr),
        .pf_done       (ic_pf_done),
        .mem_req       (ic_m_req),
        .mem_we        (),
        .mem_addr      (ic_m_addr),
        .mem_wdata     (),
        .mem_wstrb     (),
        .mem_resp      (ic_m_resp),
        .mem_rdata     (ic_m_rdata),
        .inv_req       (inv_req),
        .inv_addr      (inv_addr),
        .inv_dirty     (ic_inv_dirty),
        .inv_line      (ic_inv_line),
        .rc_req        (),
        .rc_addr       (),
        .rc_dirty      (1'b0),
        .rc_line       ({LINE_BITS{1'b0}}),
        .cnt_access    (ic_access),
        .cnt_miss      (ic_miss),
        .cnt_store_miss (),
        .cnt_writeback (),
        .cnt_pf_issued (ic_pf_issued),
        .cnt_pf_useful (ic_pf_useful),
        .cnt_pf_late   (ic_pf_late),
        .cnt_evict     (ic_evict),
        .cnt_mem_read  (ic_mem_read),
        .cnt_mem_write (),
        .cnt_wbuf_full (),
        .cnt_wait      (ic_wait),
        .cnt_vb_hit    (ic_vb_hit),
        .cnt_inval     (ic_inval)
    );

    assign imem_fault = !i_in_ram;
    assign imem_ready = i_in_ram ? ic_ready : 1'b1;
    assign imem_rdata = i_in_ram ? ic_rdata[31:0] : 32'b0;

    wire [63:0] i_offset = imem_addr & (LINE_BYTES - 1);
    assign imem_rdata2 = ic_rdata[63:32];
    assign imem_valid2 = i_in_ram && (i_offset <= LINE_BYTES - 8);

    // ---- data side ----
    logic        dc_ready;
    logic [63:0] dc_rdata;
    wire         d_access = dmem_re || dmem_we;

    logic        dc_pf_req, dc_pf_done;
    logic [63:0] dc_pf_addr;
    wire         dc_pf_ok = (dc_pf_addr < RAM_BYTES);

    prefetcher #(
        .MODE       (DC_PF_MODE),
        .LINE_BYTES (LINE_BYTES),
        .DISTANCE   (PF_DISTANCE)
    ) u_dpf (
        .clk       (clk),
        .rst       (rst),
        .acc_valid (d_access && d_in_ram && dc_ready),
        .acc_addr  (dmem_addr),
        .acc_pc    (dmem_pc),
        .pf_req    (dc_pf_req),
        .pf_addr   (dc_pf_addr),
        .pf_done   (dc_pf_done || (dc_pf_req && !dc_pf_ok))
    );

    cache #(
        .ENABLE     (DC_ENABLE),
        .SETS       (DC_SETS),
        .WAYS           (DC_WAYS),
        .LINE_BYTES     (LINE_BYTES),
        .REPL           (DC_REPL),
        .WRITE_HIT      (DC_WRITE_HIT),
        .WRITE_MISS     (DC_WRITE_MISS),
        .WBUF_ENTRIES   (DC_WBUF_ENTRIES),
        .WBUF_MERGE     (DC_WBUF_MERGE),
        .VICTIM_ENTRIES (DC_VICTIM_ENTRIES)
    ) u_dcache (
        .clk           (clk),
        .rst           (rst),
        .req           (d_access && d_in_ram),
        .we            (dmem_we),
        .addr          (dmem_addr),
        .size          (dmem_size),
        .wdata         (dmem_wdata),
        .ready         (dc_ready),
        .rdata         (dc_rdata),
        .lwdata        ({LINE_BITS{1'b0}}),
        .lwstrb        ({LINE_BYTES{1'b0}}),
        .lrdata        (),
        .pf_req        (dc_pf_req && dc_pf_ok),
        .pf_addr       (dc_pf_addr),
        .pf_done       (dc_pf_done),
        .mem_req       (dc_m_req),
        .mem_we        (dc_m_we),
        .mem_addr      (dc_m_addr),
        .mem_wdata     (dc_m_wdata),
        .mem_wstrb     (dc_m_wstrb),
        .mem_resp      (dc_m_resp),
        .mem_rdata     (dc_m_rdata),
        .inv_req       (inv_req),
        .inv_addr      (inv_addr),
        .inv_dirty     (dc_inv_dirty),
        .inv_line      (dc_inv_line),
        .rc_req        (),
        .rc_addr       (),
        .rc_dirty      (1'b0),
        .rc_line       ({LINE_BITS{1'b0}}),
        .cnt_access    (dc_access),
        .cnt_miss      (dc_miss),
        .cnt_store_miss (dc_store_miss),
        .cnt_writeback (dc_writeback),
        .cnt_pf_issued (dc_pf_issued),
        .cnt_pf_useful (dc_pf_useful),
        .cnt_pf_late   (dc_pf_late),
        .cnt_evict     (dc_evict),
        .cnt_mem_read  (dc_mem_read),
        .cnt_mem_write (dc_mem_write),
        .cnt_wbuf_full (dc_wbuf_full),
        .cnt_wait      (dc_wait),
        .cnt_vb_hit    (dc_vb_hit),
        .cnt_inval     (dc_inval)
    );

    assign dmem_fault = d_access && !d_in_ram && !(dmem_we && d_mmio);
    assign dmem_ready = d_in_ram ? dc_ready : 1'b1;
    assign dmem_rdata = d_in_ram ? dc_rdata : 64'b0;

    // ---- below the first-level caches ----
    generate
        if (L2_ENABLE == 0) begin : g_direct
            // Each cache has its own memory port.
            assign ma_req     = ic_m_req;
            assign ma_addr    = ic_m_addr;
            assign ic_m_resp  = ma_resp;
            assign ic_m_rdata = ma_rdata;
            assign mb_req     = dc_m_req;
            assign mb_we      = dc_m_we;
            assign mb_addr    = dc_m_addr;
            assign mb_wdata   = dc_m_wdata;
            assign mb_wstrb   = dc_m_wstrb;
            assign dc_m_resp  = mb_resp;
            assign dc_m_rdata = mb_rdata;
            assign inv_req    = 1'b0;
            assign inv_addr   = 64'b0;
            assign l2_access    = 64'd0;
            assign l2_miss      = 64'd0;
            assign l2_store_miss = 64'd0;
            assign l2_writeback = 64'd0;
            assign l2_evict     = 64'd0;
            assign l2_mem_read  = 64'd0;
            assign l2_mem_write = 64'd0;
            assign l2_wait      = 64'd0;
            assign ic_arb_wait  = 64'd0;
            assign dc_arb_wait  = 64'd0;
        end else begin : g_l2
            logic                  l2_req, l2_we, l2_ready;
            logic [63:0]           l2_addr;
            logic [LINE_BITS-1:0]  l2_wdata, l2_rdata;
            logic [LINE_BYTES-1:0] l2_wstrb;

            line_arbiter #(
                .LINE_BYTES (LINE_BYTES)
            ) u_arb (
                .clk        (clk),
                .rst        (rst),
                .a_req      (ic_m_req),
                .a_addr     (ic_m_addr),
                .a_resp     (ic_m_resp),
                .a_rdata    (ic_m_rdata),
                .b_req      (dc_m_req),
                .b_we       (dc_m_we),
                .b_addr     (dc_m_addr),
                .b_wdata    (dc_m_wdata),
                .b_wstrb    (dc_m_wstrb),
                .b_resp     (dc_m_resp),
                .b_rdata    (dc_m_rdata),
                .dn_req     (l2_req),
                .dn_we      (l2_we),
                .dn_addr    (l2_addr),
                .dn_wdata   (l2_wdata),
                .dn_wstrb   (l2_wstrb),
                .dn_ready   (l2_ready),
                .dn_rdata   (l2_rdata),
                .cnt_a_wait (ic_arb_wait),
                .cnt_b_wait (dc_arb_wait)
            );

            // A dirty copy can only come from the data cache.
            wire                 up_dirty = ic_inv_dirty || dc_inv_dirty;
            wire [LINE_BITS-1:0] up_line  = dc_inv_dirty ? dc_inv_line : ic_inv_line;

            cache #(
                .SETS         (L2_SETS),
                .WAYS         (L2_WAYS),
                .LINE_BYTES   (LINE_BYTES),
                .REPL         (L2_REPL),
                .WRITE_HIT    (L2_WRITE_HIT),
                .WRITE_MISS   (L2_WRITE_MISS),
                .WBUF_ENTRIES (L2_WBUF_ENTRIES),
                .WBUF_MERGE   (L2_WBUF_MERGE),
                .LINE_PORT    (1),
                .HIT_LATENCY  (L2_HIT_LATENCY - 1),
                .HIT_JITTER   (L2_HIT_JITTER),
                .INCLUSION    (L2_INCLUSION)
            ) u_l2 (
                .clk           (clk),
                .rst           (rst),
                .req           (l2_req),
                .we            (l2_we),
                .addr          (l2_addr),
                .size          (2'd0),
                .wdata         (64'b0),
                .ready         (l2_ready),
                .rdata         (),
                .lwdata        (l2_wdata),
                .lwstrb        (l2_wstrb),
                .lrdata        (l2_rdata),
                .pf_req        (1'b0),
                .pf_addr       (64'b0),
                .pf_done       (),
                .mem_req       (mb_req),
                .mem_we        (mb_we),
                .mem_addr      (mb_addr),
                .mem_wdata     (mb_wdata),
                .mem_wstrb     (mb_wstrb),
                .mem_resp      (mb_resp),
                .mem_rdata     (mb_rdata),
                .inv_req       (1'b0),
                .inv_addr      (64'b0),
                .inv_dirty     (),
                .inv_line      (),
                .rc_req        (inv_req),
                .rc_addr       (inv_addr),
                .rc_dirty      (up_dirty),
                .rc_line       (up_line),
                .cnt_access    (l2_access),
                .cnt_miss      (l2_miss),
                .cnt_store_miss (l2_store_miss),
                .cnt_writeback (l2_writeback),
                .cnt_pf_issued (),
                .cnt_pf_useful (),
                .cnt_pf_late   (),
                .cnt_evict     (l2_evict),
                .cnt_mem_read  (l2_mem_read),
                .cnt_mem_write (l2_mem_write),
                .cnt_wbuf_full (),
                .cnt_wait      (l2_wait),
                .cnt_vb_hit    (),
                .cnt_inval     ()
            );

            assign ma_req  = 1'b0;
            assign ma_addr = 64'b0;

            initial begin
                if (L2_HIT_LATENCY < 1) begin
                    $display("ERROR mem_subsystem: L2_HIT_LATENCY must be at least 1");
                    $finish;
                end
            end

            // Simulation only: the inclusion property.
            always @(posedge clk) begin
                if (!rst && L2_INCLUSION != 0) begin
                    if (u_icache.install && !u_l2.sim_has(u_icache.fill_line_addr)) begin
                        $display("ERROR inclusion: the I cache installs line %0h, which is not in the L2",
                                 u_icache.fill_line_addr);
                        $finish;
                    end
                    if (u_dcache.install && !u_l2.sim_has(u_dcache.fill_line_addr)) begin
                        $display("ERROR inclusion: the D cache installs line %0h, which is not in the L2",
                                 u_dcache.fill_line_addr);
                        $finish;
                    end
                    if (u_l2.start_evicts && (u_icache.sim_has(u_l2.start_victim_addr) ||
                                              u_dcache.sim_has(u_l2.start_victim_addr))) begin
                        $display("ERROR inclusion: the L2 evicts line %0h, which a first-level cache still holds",
                                 u_l2.start_victim_addr);
                        $finish;
                    end
                end
            end

            // The same property checked over everything, for a testbench to
            // call when it likes.
            function automatic integer inclusion_errors();
                integer bad;
                bad = 0;
                if (L2_INCLUSION != 0) begin
                    if (IC_ENABLE != 0) begin
                        for (int e = 0; e < IC_SETS * IC_WAYS; e++)
                            if (u_icache.valid[e] && !u_l2.sim_has(u_icache.sim_addr(e)))
                                bad = bad + 1;
                        for (int k = 0; k < IC_VICTIM_ENTRIES; k++)
                            if (u_icache.vb_valid[k] && !u_l2.sim_has(u_icache.vb_addr[k]))
                                bad = bad + 1;
                    end
                    if (DC_ENABLE != 0) begin
                        for (int e = 0; e < DC_SETS * DC_WAYS; e++)
                            if (u_dcache.valid[e] && !u_l2.sim_has(u_dcache.sim_addr(e)))
                                bad = bad + 1;
                        for (int k = 0; k < DC_VICTIM_ENTRIES; k++)
                            if (u_dcache.vb_valid[k] && !u_l2.sim_has(u_dcache.vb_addr[k]))
                                bad = bad + 1;
                    end
                end
                return bad;
            endfunction
        end
    endgenerate
endmodule
