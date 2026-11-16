// Unit testbench for rtl/common/prefetcher.sv.

`timescale 1ns / 1ps

module prefetcher_tb;
    parameter int MODE       = 2;
    parameter int LINE_BYTES = 32;
    parameter int ENTRIES    = 4;
    parameter int DISTANCE   = 4;

    logic clk = 1'b0;
    logic rst = 1'b1;
    always #5 clk = ~clk;

    logic        acc_valid = 1'b0, pf_req, pf_done = 1'b0;
    logic [63:0] acc_addr = 64'b0, acc_pc = 64'b0, pf_addr;

    prefetcher #(
        .MODE (MODE), .LINE_BYTES (LINE_BYTES), .ENTRIES (ENTRIES), .DISTANCE (DISTANCE)
    ) dut (
        .clk (clk), .rst (rst),
        .acc_valid (acc_valid), .acc_addr (acc_addr), .acc_pc (acc_pc),
        .pf_req (pf_req), .pf_addr (pf_addr), .pf_done (pf_done)
    );

    integer errors = 0, checks = 0;

    // One access by the instruction at `pc`, then check what is proposed.
    task automatic access(input logic [63:0] pc, input logic [63:0] addr,
                          input logic want, input logic [63:0] addr_want,
                          input string what);
        @(negedge clk);
        acc_valid = 1'b1; acc_pc = pc; acc_addr = addr;
        @(negedge clk);
        acc_valid = 1'b0;
        checks = checks + 1;
        if (pf_req !== want || (want && pf_addr !== addr_want)) begin
            errors = errors + 1;
            $display("wrong proposal (%s): access %h by pc %h gave req=%b addr=%h, expected req=%b addr=%h",
                     what, addr, pc, pf_req, pf_addr, want, addr_want);
        end
        pf_done = 1'b1;
        @(negedge clk);
        pf_done = 1'b0;
    endtask

    localparam logic [63:0] P1 = 64'h100;       // three instructions in
    localparam logic [63:0] P2 = 64'h104;       // different table entries
    localparam logic [63:0] P3 = 64'h108;
    localparam logic [63:0] P1_ALIAS = P1 + 4 * ENTRIES;    // shares P1's entry

    initial begin
        #1;
        // Table storage would be X in simulation and never match.
        for (int e = 0; e < ENTRIES; e++) begin
            dut.tags[e]   = P1 + 4 * e;
            dut.last[e]   = 64'h8000;
            dut.stride[e] = 64'd64;
            dut.conf[e]   = 2'd3;
        end
        repeat (2) @(posedge clk);
        @(negedge clk) rst = 1'b0;

        if (MODE == 1) begin
            access(P1, 64'h1008, 1'b1, 64'h1020, "next line");
            access(P1, 64'h101f, 1'b1, 64'h1020, "last byte of the line");
            access(P2, 64'h1020, 1'b1, 64'h1040, "first byte of the next line");
        end

        if (MODE == 2) begin
            // A walk with stride 64 by one instruction: proposals start once
            // the stride has repeated twice, DISTANCE strides ahead.
            access(P1, 64'h8040, 1'b0, 64'b0, "garbage entry must not count");
            access(P1, 64'h2000, 1'b0, 64'b0, "second access");
            access(P1, 64'h2040, 1'b0, 64'b0, "stride seen once");
            access(P1, 64'h2080, 1'b0, 64'b0, "stride repeated once");
            access(P1, 64'h20c0, 1'b1, 64'h20c0 + 64 * DISTANCE, "stride repeated twice");
            access(P1, 64'h2100, 1'b1, 64'h2100 + 64 * DISTANCE, "steady");

            // A second instruction walking backwards by 8, interleaved.
            access(P2, 64'h5000, 1'b0, 64'b0, "other instruction, first");
            access(P1, 64'h2140, 1'b1, 64'h2140 + 64 * DISTANCE, "first one undisturbed");
            access(P2, 64'h4ff8, 1'b0, 64'b0, "other instruction, second");
            access(P2, 64'h4ff0, 1'b0, 64'b0, "other instruction, third");
            access(P2, 64'h4fe8, 1'b1, 64'h4fe8 - 8 * DISTANCE, "negative stride");

            // One surprise (the jump back at the end of a pass) does not stop
            // it: the next regular step proposes again.
            access(P1, 64'h2000, 1'b0, 64'b0, "jump back to the start");
            access(P1, 64'h2040, 1'b1, 64'h2040 + 64 * DISTANCE, "resumes at once");

            // No pattern, no proposals.
            access(P3, 64'h6000, 1'b0, 64'b0, "irregular 1");
            access(P3, 64'h6100, 1'b0, 64'b0, "irregular 2");
            access(P3, 64'h6038, 1'b0, 64'b0, "irregular 3");
            access(P3, 64'h7000, 1'b0, 64'b0, "irregular 4");
            access(P3, 64'h6008, 1'b0, 64'b0, "irregular 5");
            access(P3, 64'h6800, 1'b0, 64'b0, "irregular 6");

            // The same address again and again is a stride of zero.
            access(P3, 64'h6800, 1'b0, 64'b0, "same address 1");
            access(P3, 64'h6800, 1'b0, 64'b0, "same address 2");
            access(P3, 64'h6800, 1'b0, 64'b0, "same address 3");
            access(P3, 64'h6800, 1'b0, 64'b0, "same address 4");

            // A change of stride is followed after the old one has been
            // forgotten, and never with the old stride in between.
            access(P1, 64'h3000, 1'b0, 64'b0, "new pattern 1");
            access(P1, 64'h3100, 1'b0, 64'b0, "new pattern 2");
            access(P1, 64'h3200, 1'b0, 64'b0, "new pattern 3");
            access(P1, 64'h3300, 1'b0, 64'b0, "new pattern 4");
            access(P1, 64'h3400, 1'b0, 64'b0, "new pattern 5");
            access(P1, 64'h3500, 1'b1, 64'h3500 + 256 * DISTANCE, "new stride learned");

            // Another instruction that lands on the same table entry takes it
            // over and does not inherit the pattern.
            access(P1_ALIAS, 64'h3600, 1'b0, 64'b0, "different instruction, same entry");
        end

        if (MODE == 0) begin
            access(P1, 64'h1008, 1'b0, 64'b0, "off");
            access(P1, 64'h1048, 1'b0, 64'b0, "off");
        end

        if (errors == 0) $display("PASS %0d checks", checks);
        else             $display("FAIL %0d of %0d checks", errors, checks);
        $finish;
    end
endmodule
