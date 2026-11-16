// Branch predictor: a branch target buffer (BTB) with a saturating counter in
// each entry.

module bpred #(
    parameter int MODE         = 1,
    parameter int ENTRIES      = 64,    // power of two
    parameter int TAG_BITS     = 0,     // 0 = full tag; a few bits = aliasing
    parameter int COUNTER_BITS = 2
) (
    input  logic        clk,
    input  logic        rst,

    // lookup, in the fetch stage
    input  logic [63:0] pc,
    output logic        pred_taken,
    output logic [63:0] pred_target,
    input  logic [63:0] pc2,            // second lookup
    output logic        pred2_taken,
    output logic [63:0] pred2_target,

    // training, from the execute stage
    input  logic        upd_valid,      // an instruction resolved this cycle
    input  logic [63:0] upd_pc,
    input  logic        upd_taken,      // it transferred control
    input  logic [63:0] upd_target,     // to this address
    input  logic        upd2_valid,     // a second, younger one resolved with it
    input  logic [63:0] upd2_pc,
    input  logic        upd2_taken,
    input  logic [63:0] upd2_target
);
    localparam int IDX_BITS = $clog2(ENTRIES);          // 0 when ENTRIES = 1
    localparam logic [63:0] TAG_MASK =
        (TAG_BITS == 0) ? 64'hFFFF_FFFF_FFFF_FFFF : ((64'd1 << TAG_BITS) - 64'd1);
    localparam logic [COUNTER_BITS-1:0] COUNT_MAX   = {COUNTER_BITS{1'b1}};

    logic [ENTRIES-1:0]      valid;
    logic [63:0]             tags    [0:ENTRIES-1];
    logic [63:0]             targets [0:ENTRIES-1];
    logic [COUNTER_BITS-1:0] counter [0:ENTRIES-1];

    // A free-running pseudo-random number for the garbage predictions of MODE
    // 2.
    logic [31:0] lfsr;
    always_ff @(posedge clk) begin
        if (rst) lfsr <= 32'hACE1_2B4D;
        else     lfsr <= {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};
    end

    // ---- lookup (one copy of this logic per lookup port) ----
    logic [1:0]  look_taken;
    logic [63:0] look_target [0:1];

    for (genvar i = 0; i < 2; i++) begin : g_lookup
        wire [63:0] l_pc    = (i == 0) ? pc : pc2;
        wire [63:0] l_word  = l_pc >> 2;
        wire [63:0] l_index = l_word & (ENTRIES - 1);
        wire [31:0] l_entry = l_index[31:0];
        wire [63:0] l_tag   = (l_word >> IDX_BITS) & TAG_MASK;

        wire [COUNTER_BITS-1:0] l_count = counter[l_entry];
        wire        btb_hit    = valid[l_entry] && (tags[l_entry] == l_tag);
        wire        btb_taken  = btb_hit && l_count[COUNTER_BITS-1];
        wire [63:0] btb_target = targets[l_entry];

        // Garbage predictions (MODE 2): the random number mixed with the pc.
        wire [31:0] noise      = lfsr ^ l_pc[33:2];
        wire        noise_fire = (noise[1:0] == 2'b00);
        wire [2:0]  noise_kind = noise[4:2];
        wire [63:0] noise_addr = {48'b0, noise[20:7], 2'b00};
        logic [63:0] noise_target;
        always_comb begin
            case (noise_kind)
                3'd0:    noise_target = l_pc + 64'd8;                   // skip one
                3'd1:    noise_target = l_pc - 64'd4;                   // go back one
                3'd2:    noise_target = noise_addr;                     // anywhere in RAM
                3'd3:    noise_target = l_pc + 64'd2;                   // misaligned
                3'd4:    noise_target = 64'h0000_0000_0002_0000;        // outside RAM
                3'd5:    noise_target = l_pc;                           // the same instruction
                3'd6:    noise_target = l_pc + 64'd4;                   // "taken" to the next one
                default: noise_target = l_pc - 64'd64;
            endcase
        end

        assign look_taken[i]  = (MODE == 0)               ? 1'b0 :
                                (MODE == 2 && noise_fire) ? 1'b1 : btb_taken;
        assign look_target[i] = (MODE == 0)               ? 64'b0 :
                                (MODE == 2 && noise_fire) ? noise_target : btb_target;
    end

    assign pred_taken   = look_taken[0];
    assign pred_target  = look_target[0];
    assign pred2_taken  = look_taken[1];
    assign pred2_target = look_target[1];

    // ---- training (port 0 is the older instruction, port 1 the younger) ----
    logic [1:0]              u_valid, u_taken, u_hit;
    logic [63:0]             u_target [0:1];
    logic [31:0]             u_entry  [0:1];
    logic [63:0]             u_tag    [0:1];
    logic [COUNTER_BITS-1:0] u_count  [0:1];

    for (genvar i = 0; i < 2; i++) begin : g_train
        wire [63:0] t_pc    = (i == 0) ? upd_pc : upd2_pc;
        wire [63:0] t_word  = t_pc >> 2;
        wire [63:0] t_index = t_word & (ENTRIES - 1);
        wire [31:0] t_entry = t_index[31:0];
        assign u_valid[i]  = (i == 0) ? upd_valid  : upd2_valid;
        assign u_taken[i]  = (i == 0) ? upd_taken  : upd2_taken;
        assign u_target[i] = (i == 0) ? upd_target : upd2_target;
        assign u_entry[i]  = t_entry;
        assign u_tag[i]    = (t_word >> IDX_BITS) & TAG_MASK;
        assign u_hit[i]    = valid[t_entry] && (tags[t_entry] == u_tag[i]);
        assign u_count[i]  = counter[t_entry];
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            valid <= '0;
        end else if (MODE != 0) begin
            // In this order, so that the younger instruction's update is the
            // one kept if both write the same entry.
            for (int i = 0; i < 2; i++) begin
                if (u_valid[i]) begin
                    if (u_taken[i]) begin
                        targets[u_entry[i]] <= u_target[i];
                        if (u_hit[i]) begin
                            if (u_count[i] != COUNT_MAX) counter[u_entry[i]] <= u_count[i] + 1'b1;
                        end else begin
                            valid[u_entry[i]]   <= 1'b1;
                            tags[u_entry[i]]    <= u_tag[i];
                            counter[u_entry[i]] <= COUNT_MAX;
                        end
                    end else if (u_hit[i]) begin
                        if (u_count[i] != '0) counter[u_entry[i]] <= u_count[i] - 1'b1;
                    end
                end
            end
        end
    end
endmodule
