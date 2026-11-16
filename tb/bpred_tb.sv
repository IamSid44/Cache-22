// Unit testbench for rtl/common/bpred.sv.

`timescale 1ns / 1ps

module bpred_tb;
    parameter int ENTRIES      = 8;
    parameter int TAG_BITS     = 0;
    parameter int COUNTER_BITS = 2;

    logic clk = 1'b0;
    logic rst = 1'b1;
    always #5 clk = ~clk;

    logic [63:0] pc = 64'b0, upd_pc = 64'b0, upd_target = 64'b0, pred_target;
    logic        pred_taken, upd_valid = 1'b0, upd_taken = 1'b0;
    // the second lookup and training ports
    logic [63:0] pc2 = 64'b0, upd2_pc = 64'b0, upd2_target = 64'b0, pred2_target;
    logic        pred2_taken, upd2_valid = 1'b0, upd2_taken = 1'b0;

    bpred #(
        .MODE         (1),
        .ENTRIES      (ENTRIES),
        .TAG_BITS     (TAG_BITS),
        .COUNTER_BITS (COUNTER_BITS)
    ) dut (
        .clk (clk), .rst (rst),
        .pc (pc), .pred_taken (pred_taken), .pred_target (pred_target),
        .pc2 (pc2), .pred2_taken (pred2_taken), .pred2_target (pred2_target),
        .upd_valid (upd_valid), .upd_pc (upd_pc),
        .upd_taken (upd_taken), .upd_target (upd_target),
        .upd2_valid (upd2_valid), .upd2_pc (upd2_pc),
        .upd2_taken (upd2_taken), .upd2_target (upd2_target)
    );

    integer errors = 0, checks = 0;

    // Tell the predictor what the instruction at `a` did.
    task automatic train(input logic [63:0] a, input logic taken, input logic [63:0] target);
        @(negedge clk);
        upd_valid = 1'b1; upd_pc = a; upd_taken = taken; upd_target = target;
        @(negedge clk);
        upd_valid = 1'b0;
    endtask

    // Check the prediction for the instruction at `a`.
    task automatic expect_pred(input logic [63:0] a, input logic taken,
                               input logic [63:0] target, input string what);
        pc = a;
        #1;
        checks = checks + 1;
        if (pred_taken !== taken || (taken && pred_target !== target)) begin
            errors = errors + 1;
            $display("wrong prediction (%s): pc %h gave taken=%b target=%h, expected taken=%b target=%h",
                     what, a, pred_taken, pred_target, taken, target);
        end
    endtask

    localparam logic [63:0] A  = 64'h1000;                  // a branch
    localparam logic [63:0] B  = A + 64'd4;                 // its neighbour: another entry
    localparam logic [63:0] C  = A + 4 * ENTRIES;           // same entry as A, tag differs by 1
    localparam logic [63:0] D  = A + 8 * ENTRIES;           // same entry as A, tag differs by 2
    localparam logic [63:0] T1 = 64'h2000;
    localparam logic [63:0] T2 = 64'h3000;
    localparam logic [63:0] T3 = 64'h4000;

    initial begin
        #1;
        // Storage holds anything before reset; in simulation it would be X, and
        // an X tag never matches, which would hide a missing valid check.
        for (int e = 0; e < ENTRIES; e++) begin
            dut.tags[e]    = ((A >> 2) >> $clog2(ENTRIES)) & dut.TAG_MASK;
            dut.targets[e] = T3;
            dut.counter[e] = '1;
        end
        repeat (2) @(posedge clk);
        @(negedge clk) rst = 1'b0;

        expect_pred(A, 1'b0, 64'b0, "nothing known after reset");

        // A branch that is not taken does not get an entry.
        train(A, 1'b0, T1);
        expect_pred(A, 1'b0, 64'b0, "never taken");

        // First time taken: remembered, predicted taken next time.
        train(A, 1'b1, T1);
        expect_pred(A, 1'b1, T1, "taken once");
        expect_pred(B, 1'b0, 64'b0, "neighbouring instruction");

        // The target follows the last taken execution (returns, indirect jumps).
        train(A, 1'b1, T2);
        expect_pred(A, 1'b1, T2, "target changed");

        if (COUNTER_BITS == 2) begin
            // Hysteresis: the counter is now at its maximum.
            train(A, 1'b0, T2);
            expect_pred(A, 1'b1, T2, "strongly taken, wrong once");
            train(A, 1'b0, T2);
            expect_pred(A, 1'b0, 64'b0, "wrong twice in a row");
            // Back up through the weak states.
            train(A, 1'b1, T2);
            expect_pred(A, 1'b1, T2, "weakly not taken, then taken");
            // Saturation at the bottom: many not-taken outcomes count as two,
            // not more.
            repeat (6) train(A, 1'b0, T2);
            train(A, 1'b1, T2);
            expect_pred(A, 1'b0, 64'b0, "strongly not taken, taken once");
            train(A, 1'b1, T2);
            expect_pred(A, 1'b1, T2, "strongly not taken, taken twice");
            // Saturation at the top.
            repeat (6) train(A, 1'b1, T2);
            train(A, 1'b0, T2);
            expect_pred(A, 1'b1, T2, "saturated taken, wrong once");
            train(A, 1'b1, T2);
        end else begin
            // One bit: the prediction is simply the last outcome.
            train(A, 1'b0, T2);
            expect_pred(A, 1'b0, 64'b0, "last time not taken");
            train(A, 1'b1, T2);
            expect_pred(A, 1'b1, T2, "last time taken");
        end

        // A loop branch: taken 9 times, then not taken, three times over.
        begin
            integer wrong;
            wrong = 0;
            for (int rep = 0; rep < 3; rep++) begin
                for (int i = 0; i < 10; i++) begin
                    pc = B;
                    #1;
                    if (rep > 0 && pred_taken !== (i < 9)) wrong = wrong + 1;
                    train(B, (i < 9), T1);
                end
            end
            checks = checks + 1;
            // 2-bit: only the exit is mispredicted. 1-bit: the exit, and then
            // the first iteration of the next execution.
            if (wrong != ((COUNTER_BITS == 2) ? 2 : 4)) begin
                errors = errors + 1;
                $display("loop branch: %0d mispredictions in two executions, expected %0d",
                         wrong, (COUNTER_BITS == 2) ? 2 : 4);
            end
        end

        // A branch that alternates taken / not taken.
        begin
            integer wrong;
            wrong = 0;
            train(B, 1'b1, T1);
            train(B, 1'b1, T1);
            for (int i = 0; i < 18; i++) begin
                pc = B;
                #1;
                if (pred_taken !== i[0]) wrong = wrong + 1;
                train(B, i[0], T1);
            end
            checks = checks + 1;
            if (wrong != ((COUNTER_BITS == 2) ? 9 : 18)) begin
                errors = errors + 1;
                $display("alternating branch: %0d of 18 mispredicted, expected %0d",
                         wrong, (COUNTER_BITS == 2) ? 9 : 18);
            end
        end

        // Two branches that share an entry.
        if (TAG_BITS == 0) begin
            expect_pred(C, 1'b0, 64'b0, "same entry, different tag");
            train(C, 1'b1, T3);                     // C takes the entry over
            expect_pred(C, 1'b1, T3, "took the entry over");
            expect_pred(A, 1'b0, 64'b0, "evicted");
        end else begin
            // With TAG_BITS = 1 only the lowest tag bit is compared: C still
            // differs from A, D does not and is mistaken for A.
            expect_pred(C, 1'b0, 64'b0, "same entry, low tag bit differs");
            expect_pred(D, 1'b1, T2, "aliases with A");
            // D is really not a taken branch: that pushes the entry down.
            train(D, 1'b0, 64'b0);
            if (COUNTER_BITS == 2) train(D, 1'b0, 64'b0);
            expect_pred(A, 1'b0, 64'b0, "pushed down by its alias");
        end

        // ---- the second lookup and training ports ----
        // Start from an empty table again, with two instructions that use
        // different entries.
        @(negedge clk) rst = 1'b1;
        @(negedge clk) rst = 1'b0;

        train(P, 1'b1, T1);
        expect_both(Q, 1'b0, 64'b0, P, 1'b1, T1, "second lookup port");
        expect_both(P, 1'b1, T1, Q, 1'b0, 64'b0, "lookup ports swapped");

        train_both(1'b0, P, 1'b0, 64'b0, 1'b1, Q, 1'b1, T2);
        expect_both(P, 1'b1, T1, Q, 1'b1, T2, "trained through the second port");

        // Both ports in one cycle, different entries: both count down from the
        // maximum, which flips a 1-bit counter and not a 2-bit one.
        train_both(1'b1, P, 1'b0, 64'b0, 1'b1, Q, 1'b0, 64'b0);
        expect_both(P, (COUNTER_BITS == 2), T1, Q, (COUNTER_BITS == 2), T2,
                    "both ports at once");

        // Both ports in one cycle, the same entry: the younger instruction
        // (port 2) is the one whose update stays.
        train_both(1'b1, P, 1'b1, T3, 1'b1, P, 1'b1, T2);
        expect_both(P, 1'b1, T2, P, 1'b1, T2, "both ports, same entry");

        if (errors == 0) $display("PASS %0d checks", checks);
        else             $display("FAIL %0d of %0d checks", errors, checks);
        $finish;
    end

    localparam logic [63:0] P = 64'h8000;                   // index 0
    localparam logic [63:0] Q = P + 64'd4;                  // index 1

    // Train through both ports in the same cycle (v1/v2 say which are used).
    task automatic train_both(input logic v1, input logic [63:0] a1, input logic taken1,
                              input logic [63:0] target1,
                              input logic v2, input logic [63:0] a2, input logic taken2,
                              input logic [63:0] target2);
        @(negedge clk);
        upd_valid  = v1; upd_pc  = a1; upd_taken  = taken1; upd_target  = target1;
        upd2_valid = v2; upd2_pc = a2; upd2_taken = taken2; upd2_target = target2;
        @(negedge clk);
        upd_valid  = 1'b0;
        upd2_valid = 1'b0;
    endtask

    // Check both lookup ports at once.
    task automatic expect_both(input logic [63:0] a1, input logic taken1,
                               input logic [63:0] target1,
                               input logic [63:0] a2, input logic taken2,
                               input logic [63:0] target2, input string what);
        pc  = a1;
        pc2 = a2;
        #1;
        checks = checks + 1;
        if (pred_taken !== taken1 || (taken1 && pred_target !== target1) ||
            pred2_taken !== taken2 || (taken2 && pred2_target !== target2)) begin
            errors = errors + 1;
            $display("wrong prediction (%s): port 1 pc %h gave taken=%b target=%h, port 2 pc %h gave taken=%b target=%h",
                     what, a1, pred_taken, pred_target, a2, pred2_taken, pred2_target);
        end
    endtask
endmodule
