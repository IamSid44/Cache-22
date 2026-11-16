// Integer register file: 32 x 64 bits, x0 hard-wired to zero.

module regfile (
    input  logic        clk,
    input  logic        we,
    input  logic [4:0]  waddr,
    input  logic [63:0] wdata,
    input  logic [4:0]  raddr1,
    output logic [63:0] rdata1,
    input  logic [4:0]  raddr2,
    output logic [63:0] rdata2
);
    logic [63:0] regs [1:31];

    wire write = we && (waddr != 5'd0);

    assign rdata1 = (raddr1 == 5'd0)            ? 64'b0 :
                    (write && waddr == raddr1)  ? wdata : regs[raddr1];
    assign rdata2 = (raddr2 == 5'd0)            ? 64'b0 :
                    (write && waddr == raddr2)  ? wdata : regs[raddr2];

    always_ff @(posedge clk) begin
        if (write) regs[waddr] <= wdata;
    end

    // Registers start at zero, as the platform definition requires.
    initial begin
        for (int i = 1; i < 32; i++) regs[i] = 64'b0;
    end
endmodule
