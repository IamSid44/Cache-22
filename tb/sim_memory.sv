// Simulation model of main memory: RAM_BYTES of little-endian RAM at address 0,
// accessed a whole cache line at a time through two independent ports (A for
// the instruction side, B for the data side).

module sim_memory #(
    parameter int RAM_BYTES  = 65536,
    parameter int ADDR_BITS  = 16,          // log2(RAM_BYTES)
    parameter int LINE_BYTES = 32
) (
    input  logic                    clk,
    input  logic                    rst,

    input  logic                    a_req,
    input  logic [63:0]             a_addr,
    output logic                    a_resp,
    output logic [8*LINE_BYTES-1:0] a_rdata,

    input  logic                    b_req,
    input  logic                    b_we,
    input  logic [63:0]             b_addr,
    input  logic [8*LINE_BYTES-1:0] b_wdata,
    input  logic [LINE_BYTES-1:0]   b_wstrb,
    output logic                    b_resp,
    output logic [8*LINE_BYTES-1:0] b_rdata
);
    localparam int LINE_BITS = 8 * LINE_BYTES;

    logic [7:0] ram [0:RAM_BYTES-1];

    integer latency = 0;
    integer jitter  = 0;
    integer seed    = 1;

    initial begin
        for (int i = 0; i < RAM_BYTES; i++) ram[i] = 8'h00;
        if (!$value$plusargs("mem_latency=%d", latency)) latency = 0;
        if (!$value$plusargs("mem_jitter=%d", jitter)) jitter = 0;
        if (!$value$plusargs("mem_seed=%d", seed)) seed = 1;
    end

    // Latency of the next transfer on each port, drawn in advance so that a
    // zero-latency answer can be given in the cycle of the request.
    function automatic integer draw_delay();
        if (jitter > 0) return latency + ({$random(seed)} % (jitter + 1));
        else            return latency;
    endfunction

    // The line at each port's current address, for same-cycle answers.
    wire [ADDR_BITS-1:0] a_base = a_addr[ADDR_BITS-1:0];
    wire [ADDR_BITS-1:0] b_base = b_addr[ADDR_BITS-1:0];
    logic [LINE_BITS-1:0] a_line_now, b_line_now;
    genvar i;
    generate
        for (i = 0; i < LINE_BYTES; i++) begin : g_byte
            assign a_line_now[8*i +: 8] = ram[a_base + i];
            assign b_line_now[8*i +: 8] = ram[b_base + i];
        end
    endgenerate

    // ---- port A: read only ----
    integer               a_delay, a_count;
    logic                 a_busy, a_resp_q;
    logic [ADDR_BITS-1:0] a_base_q;
    logic [LINE_BITS-1:0] a_rdata_q;

    assign a_resp  = a_resp_q || (a_req && !a_busy && a_delay == 0);
    assign a_rdata = a_resp_q ? a_rdata_q : a_line_now;

    always_ff @(posedge clk) begin
        a_resp_q <= 1'b0;
        if (rst) begin
            a_busy  <= 1'b0;
            a_delay <= draw_delay();
        end else if (a_busy) begin
            if (a_count == 0) begin
                for (int k = 0; k < LINE_BYTES; k++) a_rdata_q[8*k +: 8] <= ram[a_base_q + k];
                a_resp_q <= 1'b1;
                a_busy   <= 1'b0;
            end else begin
                a_count <= a_count - 1;
            end
        end else if (a_req) begin
            a_delay <= draw_delay();
            if (a_delay == 1) begin
                for (int k = 0; k < LINE_BYTES; k++) a_rdata_q[8*k +: 8] <= ram[a_base + k];
                a_resp_q <= 1'b1;
            end else if (a_delay > 1) begin
                a_busy   <= 1'b1;
                a_count  <= a_delay - 2;
                a_base_q <= a_base;
            end
        end
    end

    // ---- port B: read and write ----
    integer                b_delay, b_count;
    logic                  b_busy, b_resp_q;
    logic                  b_we_q;
    logic [ADDR_BITS-1:0]  b_base_q;
    logic [LINE_BITS-1:0]  b_wdata_q, b_rdata_q;
    logic [LINE_BYTES-1:0] b_wstrb_q;

    assign b_resp  = b_resp_q || (b_req && !b_busy && b_delay == 0);
    assign b_rdata = b_resp_q ? b_rdata_q : b_line_now;

    always_ff @(posedge clk) begin
        b_resp_q <= 1'b0;
        if (rst) begin
            b_busy  <= 1'b0;
            b_delay <= draw_delay();
        end else if (b_busy) begin
            if (b_count == 0) begin
                for (int k = 0; k < LINE_BYTES; k++) begin
                    if (b_we_q) begin
                        if (b_wstrb_q[k]) ram[b_base_q + k] <= b_wdata_q[8*k +: 8];
                    end else begin
                        b_rdata_q[8*k +: 8] <= ram[b_base_q + k];
                    end
                end
                b_resp_q <= 1'b1;
                b_busy   <= 1'b0;
            end else begin
                b_count <= b_count - 1;
            end
        end else if (b_req) begin
            b_delay <= draw_delay();
            if (b_delay <= 1) begin
                // Answered in this cycle (0) or the next one (1): the transfer
                // happens at this clock edge either way.
                for (int k = 0; k < LINE_BYTES; k++) begin
                    if (b_we) begin
                        if (b_wstrb[k]) ram[b_base + k] <= b_wdata[8*k +: 8];
                    end else begin
                        b_rdata_q[8*k +: 8] <= ram[b_base + k];
                    end
                end
                if (b_delay == 1) b_resp_q <= 1'b1;
            end else begin
                b_busy    <= 1'b1;
                b_count   <= b_delay - 2;
                b_we_q    <= b_we;
                b_base_q  <= b_base;
                b_wdata_q <= b_wdata;
                b_wstrb_q <= b_wstrb;
            end
        end
    end
endmodule
