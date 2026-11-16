// Prefetcher: watches the accesses the processor makes to a cache and proposes
// lines to fetch before they are asked for.

module prefetcher #(
    parameter int MODE       = 0,
    parameter int LINE_BYTES = 32,
    parameter int ENTRIES    = 16,      // stride table size, power of two
    parameter int DISTANCE   = 4
) (
    input  logic        clk,
    input  logic        rst,

    // one pulse per completed access by the processor
    input  logic        acc_valid,
    input  logic [63:0] acc_addr,
    input  logic [63:0] acc_pc,         // instruction that made the access

    // request to the cache
    output logic        pf_req,
    output logic [63:0] pf_addr,
    input  logic        pf_done
);
    localparam int OFF_BITS = $clog2(LINE_BYTES);

    // ---- stride table ----
    logic [ENTRIES-1:0] valid;
    logic [63:0]        tags   [0:ENTRIES-1];
    logic [63:0]        last   [0:ENTRIES-1];
    logic [63:0]        stride [0:ENTRIES-1];
    logic [1:0]         conf   [0:ENTRIES-1];

    wire [63:0] t_word  = acc_pc >> 2;
    wire [63:0] t_index = t_word & (ENTRIES - 1);
    wire [31:0] entry   = t_index[31:0];
    wire        known   = valid[entry] && (tags[entry] == acc_pc);
    wire [63:0] delta   = acc_addr - last[entry];
    wire [63:0] e_stride = stride[entry];
    wire [1:0]  e_conf   = conf[entry];
    wire        repeats  = known && (delta == e_stride) && (e_stride != 64'b0);
    wire [1:0]  conf_up  = (e_conf == 2'd3) ? 2'd3 : e_conf + 2'd1;

    // ---- random proposals (MODE 3) ----
    logic [31:0] lfsr;
    always_ff @(posedge clk) begin
        if (rst) lfsr <= 32'h1BAD_5EED;
        else     lfsr <= {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};
    end
    wire [63:0] random_addr = {47'b0, lfsr[18:2]};      // some fall outside the RAM

    // ---- the proposal made this cycle, if any ----
    wire [63:0] next_line   = ((acc_addr >> OFF_BITS) + 64'd1) << OFF_BITS;
    wire [63:0] stride_addr = acc_addr + e_stride * DISTANCE;

    wire        cand_valid = (MODE == 1) ? acc_valid :
                             (MODE == 2) ? (acc_valid && repeats && (conf_up >= 2'd2)) :
                             (MODE == 3) ? (lfsr[1:0] == 2'b00) : 1'b0;
    wire [63:0] cand_addr  = (MODE == 1) ? next_line :
                             (MODE == 2) ? stride_addr : random_addr;

    always_ff @(posedge clk) begin
        if (rst) begin
            valid  <= '0;
            pf_req <= 1'b0;
        end else begin
            // the pending request
            if (cand_valid) begin
                pf_req  <= 1'b1;
                pf_addr <= cand_addr;
            end else if (pf_done) begin
                pf_req <= 1'b0;
            end

            // the stride table
            if (acc_valid && MODE == 2) begin
                last[entry] <= acc_addr;
                if (!known) begin
                    valid[entry]  <= 1'b1;
                    tags[entry]   <= acc_pc;
                    stride[entry] <= 64'b0;
                    conf[entry]   <= 2'd0;
                end else if (delta == e_stride) begin
                    conf[entry] <= conf_up;
                end else if (e_conf == 2'd0) begin
                    stride[entry] <= delta;
                end else begin
                    conf[entry] <= e_conf - 2'd1;
                end
            end
        end
    end
endmodule
