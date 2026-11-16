// Cache: set-associative, with a choice of replacement policy and write policy,
// an optional write buffer, an optional victim buffer, and a port for prefetch
// requests.

module cache #(
    parameter int ENABLE       = 1,
    parameter int SETS         = 32,    // power of two
    parameter int WAYS         = 1,
    parameter int LINE_BYTES   = 32,    // power of two, at least 8
    parameter int REPL         = 0,     // 0 LRU, 1 FIFO, 2 random, 3 pseudo-LRU
    parameter int WRITE_HIT    = 0,     // 0 write-back, 1 write-through
    parameter int WRITE_MISS   = 0,     // 0 allocate, 1 no-allocate
    parameter int WBUF_ENTRIES = 0,     // 0 = no write buffer
    parameter int WBUF_MERGE   = 0,     // 1 = merge stores to a queued line
    parameter int VICTIM_ENTRIES = 0,   // 0 = no victim buffer
    parameter int LINE_PORT    = 0,     // 1 = the upper side transfers whole lines
    parameter int HIT_LATENCY  = 0,     // cycles before a request is looked at
    parameter int HIT_JITTER   = 0,     // random extra cycles, 0..HIT_JITTER
    parameter int INCLUSION    = 0      // 1 = take a line back from above before evicting it
) (
    input  logic                    clk,
    input  logic                    rst,

    // processor side
    input  logic                    req,
    input  logic                    we,
    input  logic [63:0]             addr,
    input  logic [1:0]              size,       // log2 of the access size in bytes
    input  logic [63:0]             wdata,      // right-justified
    output logic                    ready,
    output logic [63:0]             rdata,
    // ... as a line port
    input  logic [8*LINE_BYTES-1:0] lwdata,
    input  logic [LINE_BYTES-1:0]   lwstrb,
    output logic [8*LINE_BYTES-1:0] lrdata,

    // prefetch requests
    input  logic                    pf_req,
    input  logic [63:0]             pf_addr,    // any address inside the wanted line
    output logic                    pf_done,    // request started or dropped this cycle

    // memory side
    output logic                    mem_req,
    output logic                    mem_we,
    output logic [63:0]             mem_addr,   // address of the first byte of the line
    output logic [8*LINE_BYTES-1:0] mem_wdata,
    output logic [LINE_BYTES-1:0]   mem_wstrb,
    input  logic                    mem_resp,
    input  logic [8*LINE_BYTES-1:0] mem_rdata,

    // from an inclusive cache below: give this line up
    input  logic                    inv_req,
    input  logic [63:0]             inv_addr,
    output logic                    inv_dirty,  // it was here and dirty: this is its content
    output logic [8*LINE_BYTES-1:0] inv_line,
    // to the caches above, when this cache is inclusive: the same, seen from below
    output logic                    rc_req,
    output logic [63:0]             rc_addr,
    input  logic                    rc_dirty,
    input  logic [8*LINE_BYTES-1:0] rc_line,

    // statistics
    output logic [63:0]             cnt_access,     // accesses completed
    output logic [63:0]             cnt_miss,       // accesses that did not find their line
    output logic [63:0]             cnt_store_miss, // ... of which were stores
    output logic [63:0]             cnt_writeback,  // dirty lines written back
    output logic [63:0]             cnt_pf_issued,  // prefetches started
    output logic [63:0]             cnt_pf_useful,  // prefetched lines later used
    output logic [63:0]             cnt_pf_late,    // ... of which arrived after they were needed
    output logic [63:0]             cnt_evict,      // lines evicted to make room
    output logic [63:0]             cnt_mem_read,   // lines read from memory
    output logic [63:0]             cnt_mem_write,  // writes sent to memory
    output logic [63:0]             cnt_wbuf_full,  // cycles a store waited for a full write buffer
    output logic [63:0]             cnt_wait,       // cycles a request was present and not served
    output logic [63:0]             cnt_vb_hit,     // misses served by the victim buffer
    output logic [63:0]             cnt_inval       // lines given up to the cache below
);
    localparam int LINE_BITS = 8 * LINE_BYTES;
    localparam int OFF_BITS  = $clog2(LINE_BYTES);
    localparam int IDX_BITS  = $clog2(SETS);            // 0 when SETS = 1
    localparam int LINES     = SETS * WAYS;
    localparam int LEVELS    = $clog2(WAYS);            // of the pseudo-LRU tree
    localparam int WB_N      = (WBUF_ENTRIES > 0) ? WBUF_ENTRIES : 1;
    localparam bit HAS_WBUF  = (WBUF_ENTRIES > 0);
    localparam int VB_N      = (VICTIM_ENTRIES > 0) ? VICTIM_ENTRIES : 1;
    localparam bit HAS_VB    = (VICTIM_ENTRIES > 0);

    typedef enum logic [3:0] {
        S_IDLE,         // nothing on its way
        S_WB_REQ,       // send the dirty victim to memory (no write buffer)
        S_WB_WAIT,
        S_RF_REQ,       // ask memory for the wanted line
        S_RF_WAIT,
        S_DW_WAIT,      // a store is waiting for memory (no write buffer)
        S_INV,          // inclusive: the victim is being taken back from above
        S_BP_WAIT,      // cache disabled: waiting for memory
        S_BP_DONE       // cache disabled: a read arrived before it was wanted
    } state_e;
    state_e state;

    // ------------------------------------------------------------------
    // Address fields of the processor's request
    // ------------------------------------------------------------------
    wire [63:0] line_number = addr >> OFF_BITS;
    wire [63:0] set_wide    = line_number & (SETS - 1);
    wire [31:0] set         = set_wide[31:0];
    wire [63:0] tag         = line_number >> IDX_BITS;
    wire [63:0] line_addr   = line_number << OFF_BITS;
    wire [31:0] byte_off    = addr[31:0] & (LINE_BYTES - 1);
    wire [31:0] bit_off     = byte_off << 3;

    // ... of the prefetch request
    wire [63:0] pf_line_number = pf_addr >> OFF_BITS;
    wire [63:0] pf_set_wide    = pf_line_number & (SETS - 1);
    wire [31:0] pf_set         = pf_set_wide[31:0];
    wire [63:0] pf_tag         = pf_line_number >> IDX_BITS;
    wire [63:0] pf_line_addr   = pf_line_number << OFF_BITS;

    // ... and of the line the cache below wants given up
    wire [63:0] inv_line_number = inv_addr >> OFF_BITS;
    wire [63:0] inv_set_wide    = inv_line_number & (SETS - 1);
    wire [31:0] inv_set         = inv_set_wide[31:0];
    wire [63:0] inv_tag         = inv_line_number >> IDX_BITS;
    wire [63:0] inv_line_addr   = inv_line_number << OFF_BITS;

    // ------------------------------------------------------------------
    // Storage
    // ------------------------------------------------------------------
    logic [LINE_BITS-1:0] lines [0:LINES-1];
    logic [63:0]          tags  [0:LINES-1];
    logic [LINES-1:0]     valid;
    logic [LINES-1:0]     dirty;
    logic [LINES-1:0]     prefetched;       // brought in by a prefetch, not used yet

    // Replacement state.
    logic [31:0]          age  [0:LINES-1];
    logic [WAYS-1:0]      plru [0:SETS-1];
    logic [15:0]          lfsr;

    // The victim buffer: lines the cache has evicted, each under its full
    // address.
    logic [LINE_BITS-1:0] vb_line [0:VB_N-1];
    logic [63:0]          vb_addr [0:VB_N-1];
    logic [VB_N-1:0]      vb_valid;
    logic [VB_N-1:0]      vb_dirty;
    logic [31:0]          vb_next;

    // Pseudo-LRU: every tree node on the way's path is turned to point at the
    // other side.
    function automatic logic [WAYS-1:0] plru_touch(input logic [WAYS-1:0] t,
                                                   input logic [31:0] way);
        logic [WAYS-1:0] r;
        logic [31:0]     node;
        r    = t;
        node = way + WAYS;
        for (int l = 0; l < LEVELS; l++) begin
            r[node >> 1] = ~node[0];
            node = node >> 1;
        end
        return r;
    endfunction

    // ------------------------------------------------------------------
    // Lookup in the set addressed by the processor, in the set addressed by the
    // prefetch request, and in the set of the line to be given up.
    // ------------------------------------------------------------------
    logic [WAYS-1:0] way_hit, way_valid, way_oldest, pf_way_hit, pf_way_valid, pf_way_oldest;
    logic [WAYS-1:0] inv_way_hit;
    genvar w;
    generate
        for (w = 0; w < WAYS; w++) begin : g_way
            wire [31:0] entry    = set * WAYS + w;
            wire [31:0] pf_entry = pf_set * WAYS + w;
            wire [31:0] inv_way  = inv_set * WAYS + w;
            assign way_valid[w]     = valid[entry];
            assign way_hit[w]       = valid[entry] && (tags[entry] == tag);
            assign way_oldest[w]    = (age[entry] == WAYS - 1);
            assign pf_way_valid[w]  = valid[pf_entry];
            assign pf_way_hit[w]    = valid[pf_entry] && (tags[pf_entry] == pf_tag);
            assign pf_way_oldest[w] = (age[pf_entry] == WAYS - 1);
            assign inv_way_hit[w]   = valid[inv_way] && (tags[inv_way] == inv_tag);
        end
    endgenerate

    wire [WAYS-1:0] set_plru    = plru[set];
    wire [WAYS-1:0] pf_set_plru = plru[pf_set];

    // The line that is hit, and the victim: the way the next line brought into
    // each of the two sets would take.
    logic        hit;
    logic [31:0] hit_way, victim_way, pf_victim_way, node, pf_node;
    always_comb begin
        hit           = 1'b0;
        hit_way       = 32'd0;
        victim_way    = 32'd0;
        pf_victim_way = 32'd0;
        node          = 32'd1;
        pf_node       = 32'd1;
        for (int k = 0; k < WAYS; k++) begin
            if (way_hit[k]) begin
                hit     = 1'b1;
                hit_way = k;
            end
        end
        if (REPL <= 1) begin
            // The oldest line.
            for (int k = 0; k < WAYS; k++) begin
                if (way_oldest[k])    victim_way    = k;
                if (pf_way_oldest[k]) pf_victim_way = k;
            end
        end else if (REPL == 2) begin
            victim_way    = {16'b0, lfsr} % WAYS;
            pf_victim_way = {16'b0, lfsr} % WAYS;
        end else begin
            // Follow the tree bits down from the root.
            for (int l = 0; l < LEVELS; l++) begin
                node    = (node << 1) | {31'b0, set_plru[node]};
                pf_node = (pf_node << 1) | {31'b0, pf_set_plru[pf_node]};
            end
            victim_way    = node - WAYS;
            pf_victim_way = pf_node - WAYS;
        end
        // An empty way, the lowest one, comes first.
        for (int k = WAYS - 1; k >= 0; k--) begin
            if (!way_valid[k])    victim_way    = k;
            if (!pf_way_valid[k]) pf_victim_way = k;
        end
    end

    // The line to be given up, if it is in the array.
    logic        inv_hit;
    logic [31:0] inv_entry;
    always_comb begin
        inv_hit   = 1'b0;
        inv_entry = 32'd0;
        for (int k = 0; k < WAYS; k++) begin
            if (inv_way_hit[k]) begin
                inv_hit   = 1'b1;
                inv_entry = inv_set * WAYS + k;
            end
        end
    end

    // The victim buffer is searched for the same three lines.
    logic        vb_hit, pf_vb_hit, inv_vb_hit;
    logic [31:0] vb_idx, inv_vb_idx, vb_slot;
    generate
        if (HAS_VB) begin : g_vb
            always_comb begin
                vb_hit     = 1'b0;
                vb_idx     = 32'd0;
                pf_vb_hit  = 1'b0;
                inv_vb_hit = 1'b0;
                inv_vb_idx = 32'd0;
                vb_slot    = vb_next;
                for (int k = VB_N - 1; k >= 0; k--) begin
                    if (vb_valid[k] && vb_addr[k] == line_addr) begin
                        vb_hit = 1'b1;
                        vb_idx = k;
                    end
                    if (vb_valid[k] && vb_addr[k] == pf_line_addr) pf_vb_hit = 1'b1;
                    if (vb_valid[k] && vb_addr[k] == inv_line_addr) begin
                        inv_vb_hit = 1'b1;
                        inv_vb_idx = k;
                    end
                    if (!vb_valid[k]) vb_slot = k;      // an empty entry, the lowest, first
                end
            end
        end else begin : g_no_vb
            assign vb_hit     = 1'b0;
            assign vb_idx     = 32'd0;
            assign pf_vb_hit  = 1'b0;
            assign inv_vb_hit = 1'b0;
            assign inv_vb_idx = 32'd0;
            assign vb_slot    = 32'd0;
        end
    endgenerate

    wire                 pf_present   = (|pf_way_hit) || pf_vb_hit;
    wire [31:0]          hit_entry    = set * WAYS + hit_way;
    wire [LINE_BITS-1:0] hit_line     = lines[hit_entry];

    // Giving a line up to the cache below.
    wire inv_take = (ENABLE != 0) && inv_req && (inv_hit || inv_vb_hit);
    assign inv_dirty = inv_take && (inv_vb_hit ? vb_dirty[inv_vb_idx] : dirty[inv_entry]);
    assign inv_line  = inv_vb_hit ? vb_line[inv_vb_idx] : lines[inv_entry];

    // The request as the cache acts on it: after the hit latency, and not in a
    // cycle in which a line changes hands between two caches.
    logic lat_ok;
    wire  rq = req && lat_ok && !inv_take && (state != S_INV);

    // The line that is on its way: where it will go, and which line it is.
    logic [31:0] fill_entry;
    logic [31:0] fill_set;
    logic [31:0] fill_way;
    logic [63:0] fill_tag;
    logic [63:0] fill_line_addr;
    logic        fill_is_prefetch;

    // The dirty line that is leaving, copied aside until it has been written
    // back.
    logic [LINE_BITS-1:0] evict_line;
    logic [63:0]          evict_addr;

    // Inclusive: the victim that is being taken back from the caches above, and
    // the one that has just been.
    logic [63:0] rc_addr_q;
    logic [31:0] rc_entry;
    logic        recalled;
    logic [31:0] recalled_entry;

    // Cache disabled: the request that is waiting for memory, and a read result
    // that arrived while the processor was not ready to take it.
    logic [63:0]          bp_addr;
    logic                 bp_we;
    logic [LINE_BITS-1:0] bp_line;

    // ------------------------------------------------------------------
    // Write data and strobes, positioned inside the line
    // ------------------------------------------------------------------
    wire [63:0] size_mask = (size == 2'd0) ? 64'h0000_0000_0000_00FF :
                            (size == 2'd1) ? 64'h0000_0000_0000_FFFF :
                            (size == 2'd2) ? 64'h0000_0000_FFFF_FFFF :
                                             64'hFFFF_FFFF_FFFF_FFFF;
    wire [7:0]  byte_mask = (size == 2'd0) ? 8'h01 :
                            (size == 2'd1) ? 8'h03 :
                            (size == 2'd2) ? 8'h0F : 8'hFF;

    logic [LINE_BITS-1:0]  wr_bits, wr_data;
    logic [LINE_BYTES-1:0] wr_strobe;
    genvar b;
    generate
        if (LINE_PORT != 0) begin : g_line_port
            assign wr_data   = lwdata;
            assign wr_strobe = lwstrb;
            for (b = 0; b < LINE_BYTES; b++) begin : g_byte
                assign wr_bits[8*b +: 8] = {8{lwstrb[b]}};
            end
        end else begin : g_byte_port
            assign wr_bits   = {{(LINE_BITS-64){1'b0}}, size_mask} << bit_off;
            assign wr_data   = {{(LINE_BITS-64){1'b0}}, wdata} << bit_off;
            assign wr_strobe = {{(LINE_BYTES-8){1'b0}}, byte_mask} << byte_off;
        end
    endgenerate

    // ------------------------------------------------------------------
    // Write buffer: a queue of writes on their way to memory.
    // ------------------------------------------------------------------
    logic [63:0]           wb_addr [0:WB_N-1];
    logic [LINE_BITS-1:0]  wb_data [0:WB_N-1];
    logic [LINE_BYTES-1:0] wb_strb [0:WB_N-1];
    logic [31:0]           wb_head, wb_count;
    logic                  drain_wait;          // the oldest entry is on its way to memory

    wire idle    = (state == S_IDLE);
    wire filling = (state == S_WB_REQ) || (state == S_WB_WAIT) ||
                   (state == S_RF_REQ) || (state == S_RF_WAIT);

    // Is a write to the line that is to be read still queued?
    logic        rf_conflict;
    logic [31:0] rf_pos;
    generate
        if (HAS_WBUF) begin : g_conflict
            always_comb begin
                rf_conflict = 1'b0;
                rf_pos      = 32'd0;
                for (int k = 0; k < WB_N; k++) begin
                    rf_pos = (k + WB_N - wb_head) % WB_N;
                    if (rf_pos < wb_count && wb_addr[k] == fill_line_addr) rf_conflict = 1'b1;
                end
            end
        end else begin : g_no_conflict
            assign rf_conflict = 1'b0;
            assign rf_pos      = 32'd0;
        end
    endgenerate

    // The memory port is used by one thing at a time.
    wire rf_issue    = (state == S_RF_REQ) && !drain_wait && !rf_conflict;
    wire port_busy   = (state == S_WB_WAIT) || (state == S_RF_WAIT) || (state == S_DW_WAIT);
    wire drain_issue = HAS_WBUF && (wb_count != 32'd0) && !drain_wait && !port_busy &&
                       !rf_issue;
    wire drain_resp  = mem_resp && (drain_wait || drain_issue);
    wire fsm_resp    = mem_resp && !drain_wait && !drain_issue;

    // The newest queued write to the line being stored to, if merging is on.
    wire         head_locked = drain_wait || drain_issue;
    logic        merge_found;
    logic [31:0] merge_idx, merge_pos, mg_pos;
    generate
        if (HAS_WBUF && WBUF_MERGE != 0) begin : g_merge
            always_comb begin
                merge_found = 1'b0;
                merge_idx   = 32'd0;
                merge_pos   = 32'd0;
                mg_pos      = 32'd0;
                for (int k = 0; k < WB_N; k++) begin
                    mg_pos = (k + WB_N - wb_head) % WB_N;
                    if (mg_pos < wb_count && wb_addr[k] == line_addr &&
                        !(mg_pos == 32'd0 && head_locked) &&
                        (!merge_found || mg_pos > merge_pos)) begin
                        merge_found = 1'b1;
                        merge_idx   = k;
                        merge_pos   = mg_pos;
                    end
                end
            end
        end else begin : g_no_merge
            assign merge_found = 1'b0;
            assign merge_idx   = 32'd0;
            assign merge_pos   = 32'd0;
            assign mg_pos      = 32'd0;
        end
    endgenerate
    wire wb_room = (wb_count < WBUF_ENTRIES);
    wire push_ok = merge_found || wb_room;

    // ------------------------------------------------------------------
    // What starts this cycle
    // ------------------------------------------------------------------
    wire on_way    = filling && (line_addr == fill_line_addr);
    wire st_thru   = rq && we && hit && (WRITE_HIT != 0);
    wire st_around = rq && we && !hit && (WRITE_MISS != 0) && !on_way && !vb_hit;
    wire st_mem    = (ENABLE != 0) && (st_thru || st_around);
    wire st_push   = st_mem && HAS_WBUF && push_ok;                 // into the write buffer
    wire dw_issue  = st_mem && !HAS_WBUF && idle;                   // straight to memory
    // Once such a store is waiting for memory it finishes when memory answers,
    // whatever has become of its line in the meantime.
    wire dw_wait   = (state == S_DW_WAIT);
    wire dw_done   = !HAS_WBUF && mem_resp && (dw_issue || (dw_wait && req));

    // A miss on a line that the victim buffer holds: the line and the victim
    // change places.
    wire swap_start = HAS_VB && (ENABLE != 0) && idle && rq && !hit && vb_hit;

    // The dirty line that leaves the cache when a line is brought in needs a
    // place in the write buffer, if there is one.
    wire [31:0] victim_entry    = set * WAYS + victim_way;
    wire [31:0] pf_victim_entry = pf_set * WAYS + pf_victim_way;
    wire vb_out_dirty    = vb_valid[vb_slot] && vb_dirty[vb_slot];
    wire victim_writes   = valid[victim_entry] &&
                           (HAS_VB ? vb_out_dirty : dirty[victim_entry]);
    wire pf_victim_writes = valid[pf_victim_entry] &&
                            (HAS_VB ? vb_out_dirty : dirty[pf_victim_entry]);
    wire victim_room    = !HAS_WBUF || wb_room || !victim_writes;
    wire pf_victim_room = !HAS_WBUF || wb_room || !pf_victim_writes;

    // An inclusive cache first takes its victim back from the caches above (one
    // cycle, state S_INV), and evicts it in the cycle after that.
    wire miss_wanted = (ENABLE != 0) && idle && rq && !hit && !vb_hit &&
                       !(we && (WRITE_MISS != 0));
    wire miss_recall = (INCLUSION != 0) && miss_wanted && valid[victim_entry] &&
                       !(recalled && recalled_entry == victim_entry);
    wire start_miss  = miss_wanted && victim_room && !miss_recall;

    wire        pf_victim_busy  = rq && hit && (hit_entry == pf_victim_entry);
    wire pf_wanted  = (ENABLE != 0) && idle && pf_req && !pf_present && !start_miss &&
                      !pf_victim_busy && !st_mem && pf_victim_room;
    wire pf_clear   = pf_wanted && !swap_start && !miss_recall && !inv_take;
    wire pf_recall  = (INCLUSION != 0) && pf_clear && valid[pf_victim_entry] &&
                      !(recalled && recalled_entry == pf_victim_entry);
    wire pf_start   = pf_clear && !pf_recall;

    // The line taken back may come down dirty, so there has to be room for it.
    wire recall_start = (miss_recall || pf_recall) && (!HAS_WBUF || wb_room);
    assign rc_req  = (state == S_INV);
    assign rc_addr = rc_addr_q;

    wire pf_in_flight = filling && (pf_line_addr == fill_line_addr);
    assign pf_done = pf_req && ((ENABLE == 0) || pf_start || pf_present || pf_in_flight);

    wire fill_start = start_miss || pf_start;
    wire [31:0] start_way   = start_miss ? victim_way : pf_victim_way;
    wire [31:0] start_entry = start_miss ? victim_entry : pf_victim_entry;
    wire [LINE_BITS-1:0] start_victim_line = lines[start_entry];
    wire [63:0]          start_victim_tag  = tags[start_entry];
    wire [31:0]          start_set         = start_miss ? set : pf_set;
    wire [63:0]          start_victim_addr =
        ((start_victim_tag << IDX_BITS) | {32'b0, start_set}) << OFF_BITS;
    wire start_evicts = fill_start && valid[start_entry];
    wire start_dirty  = start_evicts && dirty[start_entry];

    wire                 out_leaves = HAS_VB ? (start_evicts && vb_valid[vb_slot]) : start_evicts;
    wire                 out_dirty  = HAS_VB ? (start_evicts && vb_out_dirty) : start_dirty;
    wire [LINE_BITS-1:0] out_line   = HAS_VB ? vb_line[vb_slot] : start_victim_line;
    wire [63:0]          out_addr   = HAS_VB ? vb_addr[vb_slot] : start_victim_addr;

    // ------------------------------------------------------------------
    // Outputs
    // ------------------------------------------------------------------
    wire bp_match = (addr == bp_addr) && (we == bp_we);

    logic [LINE_BITS-1:0] read_line;
    always_comb begin
        ready     = 1'b0;
        mem_req   = 1'b0;
        mem_we    = 1'b0;
        mem_addr  = line_addr;
        mem_wdata = wr_data;
        mem_wstrb = '0;
        read_line = hit_line;

        if (ENABLE != 0) begin
            // A hit is served in every state.
            if (dw_wait)         ready = dw_done;
            else if (st_mem)     ready = HAS_WBUF ? push_ok : dw_done;
            else if (rq && hit)  ready = 1'b1;

            if (state == S_WB_REQ) begin
                mem_req   = 1'b1;
                mem_we    = 1'b1;
                mem_addr  = evict_addr;
                mem_wdata = evict_line;
                mem_wstrb = '1;
            end else if (rf_issue) begin
                mem_req  = 1'b1;
                mem_addr = fill_line_addr;
            end else if (dw_issue) begin
                mem_req   = 1'b1;
                mem_we    = 1'b1;
                mem_wstrb = wr_strobe;
            end else if (drain_issue) begin
                mem_req   = 1'b1;
                mem_we    = 1'b1;
                mem_addr  = wb_addr[wb_head];
                mem_wdata = wb_data[wb_head];
                mem_wstrb = wb_strb[wb_head];
            end
        end else begin
            case (state)
                S_IDLE: begin
                    mem_req   = rq;
                    mem_we    = we;
                    mem_wstrb = wr_strobe;
                    read_line = mem_rdata;
                    ready     = rq && mem_resp;
                end
                S_BP_WAIT: begin
                    read_line = mem_rdata;
                    ready     = rq && mem_resp && bp_match;
                end
                S_BP_DONE: begin
                    read_line = bp_line;
                    ready     = rq && bp_match;
                end
                default: ;
            endcase
        end
    end

    wire [LINE_BITS-1:0] read_shifted = read_line >> bit_off;
    assign rdata  = read_shifted[63:0];
    assign lrdata = read_line;

    // The processor is waiting for exactly the line that a prefetch is bringing
    // in: the prefetch was right, but late.
    wire fill_wanted = rq && !hit && (line_addr == fill_line_addr);

    // An access that completes on a line in the cache, and the line that
    // arrives from memory.
    wire acc_done = req && ready;
    wire acc_hit  = (ENABLE != 0) && acc_done && hit;
    wire install  = ((state == S_RF_REQ && rf_issue) || state == S_RF_WAIT) && fsm_resp;

    // A hit and an install can fall into the same set in the same cycle.
    wire         hit_in_fill_set = acc_hit && (fill_set == set);
    // Scratch values for working that out, used inside the clocked block.
    logic [31:0]     new_age [0:WAYS-1];
    logic [31:0]     ref_age;
    logic [WAYS-1:0] new_plru;

    // ------------------------------------------------------------------
    // Hit latency: how long the present request has been waiting.
    // ------------------------------------------------------------------
    generate
        if (HIT_LATENCY != 0 || HIT_JITTER != 0) begin : g_latency
            logic [31:0] lat_count, lat_need;
            logic [15:0] lat_lfsr;
            assign lat_ok = (lat_count >= lat_need);
            always_ff @(posedge clk) begin
                if (rst) begin
                    lat_count <= 32'd0;
                    lat_need  <= HIT_LATENCY;
                    lat_lfsr  <= 16'h1D2B;
                end else begin
                    lat_lfsr <= {lat_lfsr[14:0], lat_lfsr[15] ^ lat_lfsr[13] ^ lat_lfsr[12] ^ lat_lfsr[10]};
                    if (!req || acc_done) begin
                        lat_count <= 32'd0;
                        lat_need  <= HIT_LATENCY + ({16'b0, lat_lfsr} % (HIT_JITTER + 1));
                    end else if (!lat_ok) begin
                        lat_count <= lat_count + 32'd1;
                    end
                end
            end
        end else begin : g_no_latency
            assign lat_ok = 1'b1;
        end
    endgenerate

    // ------------------------------------------------------------------
    // State
    // ------------------------------------------------------------------
    wire wb_push_new = (st_push && !merge_found) || (out_dirty && HAS_WBUF);
    wire [31:0] wb_tail = (wb_head + wb_count) % WB_N;

    always_ff @(posedge clk) begin
        if (rst) begin
            state         <= S_IDLE;
            valid         <= '0;
            dirty         <= '0;
            prefetched    <= '0;
            lfsr          <= 16'hACE1;
            wb_head       <= 32'd0;
            wb_count      <= 32'd0;
            drain_wait    <= 1'b0;
            vb_valid      <= '0;
            vb_dirty      <= '0;
            vb_next       <= 32'd0;
            recalled      <= 1'b0;
            cnt_access    <= 64'd0;
            cnt_miss      <= 64'd0;
            cnt_store_miss <= 64'd0;
            cnt_writeback <= 64'd0;
            cnt_pf_issued <= 64'd0;
            cnt_pf_useful <= 64'd0;
            cnt_pf_late   <= 64'd0;
            cnt_evict     <= 64'd0;
            cnt_mem_read  <= 64'd0;
            cnt_mem_write <= 64'd0;
            cnt_wbuf_full <= 64'd0;
            cnt_wait      <= 64'd0;
            cnt_vb_hit    <= 64'd0;
            cnt_inval     <= 64'd0;
            for (int e = 0; e < LINES; e++) age[e] <= e % WAYS;
            for (int s = 0; s < SETS; s++) plru[s] <= '0;
        end else begin
            if (acc_done) cnt_access <= cnt_access + 64'd1;
            if (req && !ready) cnt_wait <= cnt_wait + 64'd1;

            if (ENABLE != 0) begin
                // ---- an access completes on a line in the cache ----
                if (acc_hit) begin
                    // LRU: the line becomes the youngest of its set, and every
                    // line that was younger gets one older.
                    if (REPL == 0) begin
                        for (int k = 0; k < WAYS; k++)
                            if (age[set * WAYS + k] < age[hit_entry])
                                age[set * WAYS + k] <= age[set * WAYS + k] + 32'd1;
                        age[hit_entry] <= 32'd0;
                    end
                    if (REPL == 3) plru[set] <= plru_touch(set_plru, hit_way);
                    if (we) begin
                        lines[hit_entry] <= (hit_line & ~wr_bits) | (wr_data & wr_bits);
                        if (WRITE_HIT == 0) dirty[hit_entry] <= 1'b1;
                    end
                    if (prefetched[hit_entry]) begin
                        prefetched[hit_entry] <= 1'b0;
                        cnt_pf_useful         <= cnt_pf_useful + 64'd1;
                    end
                end
                // ---- a store completes without its line (no-allocate) ----
                if (acc_done && !hit) begin
                    cnt_miss       <= cnt_miss + 64'd1;
                    cnt_store_miss <= cnt_store_miss + 64'd1;
                end

                // ---- a line is given up to the cache below ----
                if (inv_take) begin
                    cnt_inval <= cnt_inval + 64'd1;
                    if (inv_vb_hit) begin
                        vb_valid[inv_vb_idx] <= 1'b0;
                    end else begin
                        valid[inv_entry]      <= 1'b0;
                        prefetched[inv_entry] <= 1'b0;
                    end
                end

                // ---- the write buffer ----
                if (st_push && merge_found) begin
                    wb_data[merge_idx] <= (wb_data[merge_idx] & ~wr_bits) | (wr_data & wr_bits);
                    wb_strb[merge_idx] <= wb_strb[merge_idx] | wr_strobe;
                end else if (st_push) begin
                    wb_addr[wb_tail] <= line_addr;
                    wb_data[wb_tail] <= wr_data;
                    wb_strb[wb_tail] <= wr_strobe;
                end else if (out_dirty && HAS_WBUF) begin
                    wb_addr[wb_tail] <= out_addr;
                    wb_data[wb_tail] <= out_line;
                    wb_strb[wb_tail] <= '1;
                end
                if (drain_resp) begin
                    wb_head    <= (wb_head + 32'd1) % WB_N;
                    drain_wait <= 1'b0;
                end else if (drain_issue) begin
                    drain_wait <= 1'b1;
                end
                if (HAS_WBUF) wb_count <= wb_count + {31'b0, wb_push_new} - {31'b0, drain_resp};
                if (st_mem && HAS_WBUF && !push_ok) cnt_wbuf_full <= cnt_wbuf_full + 64'd1;

                if (rf_issue) cnt_mem_read <= cnt_mem_read + 64'd1;
                if (state == S_WB_REQ || dw_issue || drain_issue)
                    cnt_mem_write <= cnt_mem_write + 64'd1;

                // A victim that was taken back is evicted in the very next
                // cycle or not on the strength of that at all.
                recalled <= 1'b0;

                // ---- bringing a line in ----
                case (state)
                    S_IDLE: begin
                        if (swap_start) begin
                            cnt_miss   <= cnt_miss + 64'd1;
                            cnt_vb_hit <= cnt_vb_hit + 64'd1;
                            if (we) cnt_store_miss <= cnt_store_miss + 64'd1;
                            lfsr <= {lfsr[14:0], lfsr[15] ^ lfsr[13] ^ lfsr[12] ^ lfsr[10]};
                            if (valid[victim_entry]) begin
                                cnt_evict        <= cnt_evict + 64'd1;
                                vb_line[vb_idx]  <= lines[victim_entry];
                                vb_addr[vb_idx]  <=
                                    ((tags[victim_entry] << IDX_BITS) | {32'b0, set}) << OFF_BITS;
                                vb_dirty[vb_idx] <= dirty[victim_entry];
                            end else begin
                                vb_valid[vb_idx] <= 1'b0;
                            end
                            lines[victim_entry]      <= vb_line[vb_idx];
                            tags[victim_entry]       <= tag;
                            valid[victim_entry]      <= 1'b1;
                            dirty[victim_entry]      <= vb_dirty[vb_idx];
                            prefetched[victim_entry] <= 1'b0;
                            for (int k = 0; k < WAYS; k++)
                                if (age[set * WAYS + k] < age[victim_entry])
                                    age[set * WAYS + k] <= age[set * WAYS + k] + 32'd1;
                            age[victim_entry] <= 32'd0;
                            if (REPL == 3) plru[set] <= plru_touch(set_plru, victim_way);
                        end else if (fill_start) begin
                            if (start_miss) cnt_miss      <= cnt_miss + 64'd1;
                            else            cnt_pf_issued <= cnt_pf_issued + 64'd1;
                            if (start_miss && we) cnt_store_miss <= cnt_store_miss + 64'd1;
                            if (start_evicts) cnt_evict     <= cnt_evict + 64'd1;
                            if (out_dirty)    cnt_writeback <= cnt_writeback + 64'd1;
                            fill_entry       <= start_entry;
                            fill_set         <= start_set;
                            fill_way         <= start_way;
                            fill_tag         <= start_miss ? tag : pf_tag;
                            fill_line_addr   <= start_miss ? line_addr : pf_line_addr;
                            fill_is_prefetch <= !start_miss;
                            lfsr <= {lfsr[14:0], lfsr[15] ^ lfsr[13] ^ lfsr[12] ^ lfsr[10]};
                            // Take the victim out of the cache now.
                            evict_line          <= out_line;
                            evict_addr          <= out_addr;
                            valid[start_entry]  <= 1'b0;
                            // With a victim buffer it goes there.
                            if (HAS_VB && start_evicts) begin
                                vb_line[vb_slot]  <= start_victim_line;
                                vb_addr[vb_slot]  <= start_victim_addr;
                                vb_valid[vb_slot] <= 1'b1;
                                vb_dirty[vb_slot] <= dirty[start_entry];
                                if (vb_valid[vb_slot]) vb_next <= (vb_next + 32'd1) % VB_N;
                            end
                            if (out_dirty && !HAS_WBUF) state <= S_WB_REQ;
                            else                        state <= S_RF_REQ;
                        end else if (recall_start) begin
                            rc_entry  <= miss_recall ? victim_entry : pf_victim_entry;
                            rc_addr_q <= miss_recall
                                ? ((tags[victim_entry] << IDX_BITS) | {32'b0, set}) << OFF_BITS
                                : ((tags[pf_victim_entry] << IDX_BITS) | {32'b0, pf_set}) << OFF_BITS;
                            state     <= S_INV;
                        end else if (dw_issue && !mem_resp) begin
                            state <= S_DW_WAIT;
                        end
                    end
                    S_INV: begin
                        // The caches above have given the line up.
                        if (rc_dirty) begin
                            lines[rc_entry] <= rc_line;
                            dirty[rc_entry] <= 1'b1;
                        end
                        recalled       <= 1'b1;
                        recalled_entry <= rc_entry;
                        state          <= S_IDLE;
                    end
                    S_WB_REQ: begin
                        if (mem_resp) state <= S_RF_REQ;
                        else          state <= S_WB_WAIT;
                    end
                    S_WB_WAIT: if (mem_resp) state <= S_RF_REQ;
                    S_RF_REQ, S_RF_WAIT: begin
                        if (install) begin
                            lines[fill_entry]      <= mem_rdata;
                            tags[fill_entry]       <= fill_tag;
                            valid[fill_entry]      <= 1'b1;
                            dirty[fill_entry]      <= 1'b0;
                            prefetched[fill_entry] <= fill_is_prefetch;
                            // The new line is the most recently used one of its
                            // set, so that it is not the next victim.
                            for (int k = 0; k < WAYS; k++) new_age[k] = age[fill_set * WAYS + k];
                            new_plru = plru[fill_set];
                            if (hit_in_fill_set && REPL == 0) begin
                                ref_age = new_age[hit_way];
                                for (int k = 0; k < WAYS; k++)
                                    if (new_age[k] < ref_age) new_age[k] = new_age[k] + 32'd1;
                                new_age[hit_way] = 32'd0;
                            end
                            if (hit_in_fill_set && REPL == 3)
                                new_plru = plru_touch(new_plru, hit_way);
                            ref_age = new_age[fill_way];
                            for (int k = 0; k < WAYS; k++)
                                if (new_age[k] < ref_age) new_age[k] = new_age[k] + 32'd1;
                            new_age[fill_way] = 32'd0;
                            for (int k = 0; k < WAYS; k++) age[fill_set * WAYS + k] <= new_age[k];
                            if (REPL == 3) plru[fill_set] <= plru_touch(new_plru, fill_way);
                            if (fill_is_prefetch && fill_wanted)
                                cnt_pf_late <= cnt_pf_late + 64'd1;
                            state <= S_IDLE;
                        end else if (rf_issue) begin
                            state <= S_RF_WAIT;
                        end
                    end
                    S_DW_WAIT: if (mem_resp) state <= S_IDLE;
                    default: state <= S_IDLE;
                endcase
            end else begin
                case (state)
                    S_IDLE: begin
                        if (rq) begin
                            cnt_miss <= cnt_miss + 64'd1;
                            if (we) begin
                                cnt_mem_write  <= cnt_mem_write + 64'd1;
                                cnt_store_miss <= cnt_store_miss + 64'd1;
                            end else begin
                                cnt_mem_read <= cnt_mem_read + 64'd1;
                            end
                            if (!mem_resp) begin
                                bp_addr <= addr;
                                bp_we   <= we;
                                state   <= S_BP_WAIT;
                            end
                        end
                    end
                    S_BP_WAIT: begin
                        if (mem_resp) begin
                            bp_line <= mem_rdata;
                            // A read that nobody is waiting for any more is
                            // kept in case the same address is asked again.
                            if (!bp_we && !(rq && bp_match)) state <= S_BP_DONE;
                            else                             state <= S_IDLE;
                        end
                    end
                    S_BP_DONE: if (rq) state <= S_IDLE;
                    default: state <= S_IDLE;
                endcase
            end
        end
    end

    // ------------------------------------------------------------------
    // Simulation only: parameter checks and the event log.
    // ------------------------------------------------------------------
    initial begin
        if (REPL < 0 || REPL > 3) begin
            $display("ERROR cache: REPL must be 0..3");
            $finish;
        end
        if (REPL == 3 && (WAYS & (WAYS - 1)) != 0) begin
            $display("ERROR cache: pseudo-LRU needs a power-of-two number of ways");
            $finish;
        end
        if (VICTIM_ENTRIES != 0 && INCLUSION != 0) begin
            $display("ERROR cache: an inclusive cache cannot have a victim buffer");
            $finish;
        end
    end

    integer log_fd = 0;
    // For the testbench, which lays what the cache still holds over memory when
    // a run ends: a dirty victim is on its way to memory.
    wire sim_evicting = (state == S_WB_REQ) || (state == S_WB_WAIT);
    // Nothing is in progress and nothing is waiting to be sent.
    wire sim_at_rest  = (idle || state == S_BP_DONE) && (wb_count == 32'd0);

    function automatic logic [63:0] sim_addr(input integer entry);
        return ((tags[entry] << IDX_BITS) | (entry / WAYS)) << OFF_BITS;
    endfunction

    function automatic logic sim_has(input logic [63:0] a);
        logic found;
        found = 1'b0;
        for (int e = 0; e < LINES; e++)
            if (valid[e] && sim_addr(e) == ((a >> OFF_BITS) << OFF_BITS)) found = 1'b1;
        for (int k = 0; k < VICTIM_ENTRIES; k++)
            if (vb_valid[k] && vb_addr[k] == ((a >> OFF_BITS) << OFF_BITS)) found = 1'b1;
        return found;
    endfunction

    task automatic log_size();
        if (LINE_PORT != 0) $fwrite(log_fd, "/%0h\n", we ? wr_strobe : {LINE_BYTES{1'b0}});
        else                $fwrite(log_fd, "%0d\n", size);
    endtask

    always @(posedge clk) begin
        if (!rst && log_fd != 0 && ENABLE != 0) begin
            if (state == S_INV)
                $fwrite(log_fd, "B %0h %s\n", rc_addr_q, rc_dirty ? "d" : "c");
            if (drain_issue) $fwrite(log_fd, "w %0h\n", wb_addr[wb_head]);
            if (state == S_WB_REQ) $fwrite(log_fd, "w %0h\n", evict_addr);
            if (drain_resp)
                $fwrite(log_fd, "M %0h %0h\n", wb_addr[wb_head], wb_strb[wb_head]);
            if ((state == S_WB_REQ || state == S_WB_WAIT) && mem_resp)
                $fwrite(log_fd, "M %0h %0h\n", evict_addr, {LINE_BYTES{1'b1}});
            if (rf_issue) $fwrite(log_fd, "R %0h\n", fill_line_addr);
            if (swap_start) begin
                $fwrite(log_fd, "S %s %0h ", we ? "w" : "r", line_addr);
                if (valid[victim_entry])
                    $fwrite(log_fd, "%0h %s\n",
                            ((tags[victim_entry] << IDX_BITS) | {32'b0, set}) << OFF_BITS,
                            dirty[victim_entry] ? "d" : "c");
                else
                    $fwrite(log_fd, "- -\n");
            end
            if (fill_start) begin
                $fwrite(log_fd, "F %s %0h ", pf_start ? "p" : we ? "w" : "r",
                        start_miss ? line_addr : pf_line_addr);
                if (start_evicts)
                    $fwrite(log_fd, "%0h %s", start_victim_addr, start_dirty ? "d" : "c");
                else
                    $fwrite(log_fd, "- -");
                if (HAS_VB && out_leaves)
                    $fwrite(log_fd, " %0h %s", out_addr, out_dirty ? "d" : "c");
                else if (HAS_VB)
                    $fwrite(log_fd, " - -");
                $fwrite(log_fd, "\n");
            end
            if (acc_hit) begin
                $fwrite(log_fd, "H %s %0h ", we ? "w" : "r", addr);
                log_size();
            end
            if (acc_done && !hit) begin
                $fwrite(log_fd, "N %0h ", addr);
                log_size();
            end
            if (dw_done) $fwrite(log_fd, "M %0h %0h\n", line_addr, wr_strobe);
            if (install) $fwrite(log_fd, "I %0h\n", fill_line_addr);
            if (inv_take)
                $fwrite(log_fd, "X %0h %s\n", inv_line_addr, inv_dirty ? "d" : "c");
        end
    end
endmodule
