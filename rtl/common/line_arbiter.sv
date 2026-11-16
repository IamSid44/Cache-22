// Line arbiter: lets two caches share the one cache below them.

module line_arbiter #(
    parameter int LINE_BYTES = 32
) (
    input  logic                    clk,
    input  logic                    rst,

    // upper port a (read only)
    input  logic                    a_req,
    input  logic [63:0]             a_addr,
    output logic                    a_resp,
    output logic [8*LINE_BYTES-1:0] a_rdata,

    // upper port b
    input  logic                    b_req,
    input  logic                    b_we,
    input  logic [63:0]             b_addr,
    input  logic [8*LINE_BYTES-1:0] b_wdata,
    input  logic [LINE_BYTES-1:0]   b_wstrb,
    output logic                    b_resp,
    output logic [8*LINE_BYTES-1:0] b_rdata,

    // lower port
    output logic                    dn_req,
    output logic                    dn_we,
    output logic [63:0]             dn_addr,
    output logic [8*LINE_BYTES-1:0] dn_wdata,
    output logic [LINE_BYTES-1:0]   dn_wstrb,
    input  logic                    dn_ready,
    input  logic [8*LINE_BYTES-1:0] dn_rdata,

    // statistics: cycles a request waited because the other cache had the port
    output logic [63:0]             cnt_a_wait,
    output logic [63:0]             cnt_b_wait
);
    localparam int LINE_BITS = 8 * LINE_BYTES;

    // The request each cache has outstanding.
    logic                  a_held, b_held;
    logic [63:0]           a_addr_q, b_addr_q;
    logic                  b_we_q;
    logic [LINE_BITS-1:0]  b_wdata_q;
    logic [LINE_BYTES-1:0] b_wstrb_q;

    logic busy;         // a request has been passed down and not answered yet
    logic cur_b;        // ... and it is b's
    logic last_b;       // the request served last was b's

    wire serve  = a_held || b_held;
    // Whose request is passed down in this cycle.
    wire pick_b = busy ? cur_b : (a_held && b_held) ? !last_b : b_held;

    assign dn_req   = serve;
    assign dn_we    = pick_b && b_we_q;
    assign dn_addr  = pick_b ? b_addr_q : a_addr_q;
    assign dn_wdata = b_wdata_q;
    assign dn_wstrb = b_wstrb_q;

    assign a_resp  = serve && !pick_b && dn_ready;
    assign b_resp  = serve && pick_b && dn_ready;
    assign a_rdata = dn_rdata;
    assign b_rdata = dn_rdata;

    always_ff @(posedge clk) begin
        if (rst) begin
            a_held     <= 1'b0;
            b_held     <= 1'b0;
            busy       <= 1'b0;
            cur_b      <= 1'b0;
            last_b     <= 1'b0;
            cnt_a_wait <= 64'd0;
            cnt_b_wait <= 64'd0;
        end else begin
            if (a_req) begin
                a_held   <= 1'b1;
                a_addr_q <= a_addr;
            end else if (a_resp) begin
                a_held <= 1'b0;
            end
            if (b_req) begin
                b_held    <= 1'b1;
                b_we_q    <= b_we;
                b_addr_q  <= b_addr;
                b_wdata_q <= b_wdata;
                b_wstrb_q <= b_wstrb;
            end else if (b_resp) begin
                b_held <= 1'b0;
            end
            if (serve) begin
                last_b <= pick_b;
                cur_b  <= pick_b;
                busy   <= !dn_ready;
            end
            if (a_held && pick_b)  cnt_a_wait <= cnt_a_wait + 64'd1;
            if (b_held && !pick_b) cnt_b_wait <= cnt_b_wait + 64'd1;
        end
    end

    // Simulation only: neither cache is passed over twice.
    integer a_passed = 0, b_passed = 0;
    always @(posedge clk) begin
        if (rst) begin
            a_passed = 0;
            b_passed = 0;
        end else if (serve && !busy) begin
            if (pick_b) begin
                b_passed = 0;
                if (a_held) a_passed = a_passed + 1;
            end else begin
                a_passed = 0;
                if (b_held) b_passed = b_passed + 1;
            end
            if (a_passed > 1 || b_passed > 1) begin
                $display("ERROR line_arbiter: a waiting cache was passed over twice");
                $finish;
            end
        end
    end
endmodule
