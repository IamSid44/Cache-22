#!/usr/bin/env python3
"""Mutation check: is the regression able to notice a broken core?

    python tools/mutation.py            # all mutations, about four and a half hours
    python tools/mutation.py trap       # only those whose name contains "trap"
    python tools/mutation.py ooo --file # only those planted in a file named *ooo*
    python tools/mutation.py --hier     # only those planted for the cache hierarchy
    python tools/mutation.py --check    # is a planted bug still in the RTL?
"""

import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = "rtl/inorder/inorder_core.sv"
OOO = "rtl/ooo/ooo_core.sv"
EXEC = "rtl/ooo/exec_unit.sv"
MEMSYS = "rtl/common/mem_subsystem.sv"
ALU = "rtl/common/alu.sv"
CSR = "rtl/common/csr_file.sv"
DECODER = "rtl/common/decoder.sv"
CACHE = "rtl/common/cache.sv"
BPRED = "rtl/common/bpred.sv"
PREFETCHER = "rtl/common/prefetcher.sv"
ARBITER = "rtl/common/line_arbiter.sv"
# Files that also have unit testbenches, and the names those tests start with.
BLOCK_NAMES = {CACHE: ("cache",), BPRED: ("predictor",), PREFETCHER: ("prefetcher",)}
# A bug planted for the hierarchy is run against the cache testbench (if it is
# in the cache) and the hierarchy testbench.
HIER_BLOCKS = {CACHE: ("cache", "hierarchy"), ARBITER: ("hierarchy",), MEMSYS: ("hierarchy",)}
SEEDS = 32
BACKUP_DIR = os.path.join(ROOT, "build", "mutation_backup")

# Configurations each mutation is tried in: ideal memory with and without a
# predictor, and every cache and predictor shape with short random latency.
CONFIGS = ("ideal,ideal-bp,ideal-garbage,nocache-jitter,one-line,4way-tiny,"
           "icache-only,dcache-only,pf-garbage,ooo-tiny,ooo-small,ss-pair")
# A bug planted in the cache is tried in the configurations that have one, in
# every policy.
CACHE_CONFIGS = ("one-line,4way-tiny,icache-only,dcache-only,pf-garbage,p-fifo,p-random,"
                 "p-plru,p-wt,p-wt-buf1,p-na,p-na-buf,p-wb-buf,p-na-next")
HIER_CONFIGS = ("l2-one,l2-incl,l2-wt-wb,l2-wb-wt,l2-incl-wt,l2-only,vb-one,vb-na,vb-wt,"
                "vb-incl")


def is_hier(mutation):
    return mutation in HIER_MUTATIONS


def configs_for(mutation):
    if is_hier(mutation):
        return HIER_CONFIGS
    return CACHE_CONFIGS if mutation[1] == CACHE else CONFIGS


def blocks_for(mutation):
    """The unit testbenches a mutation is also run against, by name."""
    if is_hier(mutation):
        return HIER_BLOCKS[mutation[1]]
    return BLOCK_NAMES.get(mutation[1], ())


def cores_for(mutation):
    """The cores a mutation is tried on, in order, until one catches it."""
    if len(mutation) > 4:
        return [mutation[4]]
    if is_hier(mutation):
        return ["inorder"]
    if mutation[1] in (OOO, EXEC):
        return ["ss", "ooo"]
    if mutation[1] == MEMSYS:
        return ["ss"]
    return ["inorder"]

# (description, file, original text, replacement[, core]).
MUTATIONS = [
    ("no forwarding from WB (rs1)", CORE,
     "fwd_wb_rs1 ? wb_data : ex_rs1_val", "ex_rs1_val"),
    ("no forwarding from MEM (rs2)", CORE,
     "fwd_mem_rs2 ? mem_alu : fwd_wb_rs2", "fwd_wb_rs2"),
    ("forwarding priority reversed", CORE,
     "fwd_mem_rs2 ? mem_alu : fwd_wb_rs2 ? wb_data : ex_rs2_val",
     "fwd_wb_rs2 ? wb_data : fwd_mem_rs2 ? mem_alu : ex_rs2_val"),
    ("forwarding from x0 allowed", CORE,
     "wire fwd_mem_rs1 = mem_ctrl.reg_write && (mem_rd != 5'd0) &&",
     "wire fwd_mem_rs1 = mem_ctrl.reg_write &&"),
    ("no load-use stall", CORE,
     "assign stall = id_valid &&", "assign stall = 1'b0 && id_valid &&"),
    ("load-use ignores rs2", CORE,
     "(id_ctrl.uses_rs2 && id_rs2 == ex_rd));", "1'b0);"),
    ("branch does not squash ID", CORE,
     "            if (flush || redirect) begin\n                id_valid <= 1'b0;",
     "            if (flush) begin\n                id_valid <= 1'b0;"),
    ("branch does not squash EX", CORE,
     "if (rst || (!hold && (flush || redirect || stall))) begin",
     "if (rst || (!hold && (flush || stall))) begin"),
    ("blt compares unsigned", CORE,
     "wire br_lt  = ($signed(ex_rs1_fwd) < $signed(ex_rs2_fwd));",
     "wire br_lt  = (ex_rs1_fwd < ex_rs2_fwd);"),
    ("jalr keeps bit 0", CORE,
     "target_sum[0] & ~ex_ctrl.jalr", "target_sum[0]"),
    ("store data not forwarded", CORE,
     "mem_store_data <= ex_rs2_fwd;", "mem_store_data <= ex_rs2_val;"),
    ("lbu sign-extends", CORE,
     "{{56{ld_b[7]  & ~ld_unsigned}}, ld_b}", "{{56{ld_b[7]}}, ld_b}"),
    ("sraw shifts in zeros", ALU,
     "(op == ALU_SRA)  ? {{32{a_lo[31]}}, a_lo} :", "(op == ALU_SRA)  ? {32'b0, a_lo} :"),
    ("word shift uses 6-bit amount", ALU,
     "wire [5:0]  shamt     = word ? {1'b0, b_lo5} : b_lo6;",
     "wire [5:0]  shamt     = b_lo6;"),

    ("trap does not squash EX", CORE,
     "if (rst || (!hold && flush)) begin\n            mem_valid <= 1'b0;",
     "if (rst) begin\n            mem_valid <= 1'b0;"),
    ("trap does not squash ID", CORE,
     "            if (flush || redirect) begin\n                id_valid <= 1'b0;",
     "            if (redirect) begin\n                id_valid <= 1'b0;"),
    ("branch redirect beats trap", CORE,
     "            if (flush)                    pc_q <= flush_pc;\n"
     "            else if (redirect)            pc_q <= redirect_pc;",
     "            if (redirect)                 pc_q <= redirect_pc;\n"
     "            else if (flush)               pc_q <= flush_pc;"),
    ("misaligned store still written", CORE,
     "assign dmem_we    = mem_ctrl.store && !mem_misaligned;",
     "assign dmem_we    = mem_ctrl.store;"),
    ("trapping instruction writes rd", CORE,
     "if (trap) wb_ctrl <= '0;\n            else      wb_ctrl <= mem_ctrl;",
     "wb_ctrl <= mem_ctrl;"),
    ("no stall for a CSR result", CORE,
     "wire ex_late_result = ex_ctrl.load || ex_ctrl.csr;",
     "wire ex_late_result = ex_ctrl.load;"),
    ("store access fault ignored", CORE,
     "wire mem_access_fault = (dmem_re || dmem_we) && dmem_fault;",
     "wire mem_access_fault = dmem_re && dmem_fault;"),
    ("word access alignment too lax", CORE,
     "(mem_size == 2'd2) ? 3'b011 : 3'b111;", "(mem_size == 2'd2) ? 3'b001 : 3'b111;"),
    ("trap counted as retired", CORE,
     ".retire     (mem_valid && !trap && !hold),", ".retire     (mem_valid && !hold),"),
    ("ecall reported as breakpoint", CORE,
     "id_ctrl.ecall   ? CAUSE_ECALL_M : CAUSE_BREAKPOINT;",
     "CAUSE_BREAKPOINT;"),
    ("mret does not restore MIE", CSR,
     "mstatus_mie  <= mstatus_mpie;", "mstatus_mie  <= 1'b0;"),
    ("trap does not save MIE", CSR,
     "mstatus_mpie <= mstatus_mie;", "mstatus_mpie <= 1'b0;"),
    ("mepc keeps its low bits", CSR,
     "CSR_MEPC:     mepc_q     <= wval_aligned;", "CSR_MEPC:     mepc_q     <= wval;"),
    ("csrrs behaves as csrrw", CSR,
     "(csr_op == 2'b10) ? (csr_rdata | csr_src) :", "(csr_op == 2'b10) ? csr_src :"),
    ("reserved funct7 not illegal", DECODER,
     "bad            = !(f7_zero || (f7_alt && (f3_addsub || f3_srx)));",
     "bad            = 1'b0;"),
    ("write to read-only CSR allowed", DECODER,
     "bad            = !csr_known || (csr_writes && csr_read_only);",
     "bad            = !csr_known;"),

    ("EX not frozen during a data miss", CORE,
     "        end else if (!hold) begin\n            ex_valid <= id_valid;",
     "        end else begin\n            ex_valid <= id_valid;"),
    ("WB not frozen during a data miss", CORE,
     "        end else if (!hold) begin\n            wb_valid <= mem_valid;",
     "        end else begin\n            wb_valid <= mem_valid;"),
    ("held instruction reported twice", CORE,
     "assign commit_valid    = wb_valid && !held_q;", "assign commit_valid    = wb_valid;"),
    ("instructions counted during a miss", CORE,
     ".retire     (mem_valid && !trap && !hold),", ".retire     (mem_valid && !trap),"),
    ("no bubble while fetch waits", CORE,
     "id_valid       <= !if_wait;", "id_valid       <= 1'b1;"),
    ("pc advances while fetch waits", CORE,
     "else if (!stall && !if_wait)  pc_q <= if_pred_next;",
     "else if (!stall)              pc_q <= if_pred_next;"),
    ("store does not mark line dirty", CACHE,
     "dirty[hit_entry] <= 1'b1;", "dirty[hit_entry] <= 1'b0;"),
    ("write-back goes to the wrong address", CACHE,
     "evict_addr          <= out_addr;",
     "evict_addr          <= start_miss ? line_addr : pf_line_addr;"),
    ("tag ignored on lookup", CACHE,
     "assign way_hit[w]       = valid[entry] && (tags[entry] == tag);",
     "assign way_hit[w]       = valid[entry];"),
    ("valid bit ignored on lookup", CACHE,
     "assign way_hit[w]       = valid[entry] && (tags[entry] == tag);",
     "assign way_hit[w]       = (tags[entry] == tag);"),
    ("store overwrites the whole line", CACHE,
     "lines[hit_entry] <= (hit_line & ~wr_bits) | (wr_data & wr_bits);",
     "lines[hit_entry] <= wr_data;"),
    ("stale answer after an abandoned read", CACHE,
     "ready     = rq && mem_resp && bp_match;", "ready     = rq && mem_resp;"),
    ("uncached store ignores byte offset", CACHE,
     "assign wr_strobe = {{(LINE_BYTES-8){1'b0}}, byte_mask} << byte_off;",
     "assign wr_strobe = {{(LINE_BYTES-8){1'b0}}, byte_mask};"),
    ("evicts the most recently used line", CACHE,
     "assign way_oldest[w]    = (age[entry] == WAYS - 1);",
     "assign way_oldest[w]    = (age[entry] == 0);"),

    ("fetch ignores the prediction", CORE,
     "else if (!stall && !if_wait)  pc_q <= if_pred_next;",
     "else if (!stall && !if_wait)  pc_q <= pc_q + 64'd4;"),
    ("prediction not kept with its instruction", CORE,
     "ex_pred_next <= id_pred_next;", "ex_pred_next <= if_pred_next;"),
    ("wrong prediction for a non-branch ignored", CORE,
     "assign redirect    = ex_valid && (ex_next_pc != ex_pred_next);",
     "assign redirect    = ex_valid && (ex_ctrl.branch || ex_ctrl.jal || ex_ctrl.jalr) &&\n"
     "                         (ex_next_pc != ex_pred_next);"),
    ("predicted-taken branch not corrected", CORE,
     "assign redirect    = ex_valid && (ex_next_pc != ex_pred_next);",
     "assign redirect    = ex_valid && ex_transfers && (ex_next_pc != ex_pred_next);"),
    ("predicted target not checked", CORE,
     "assign redirect    = ex_valid && (ex_next_pc != ex_pred_next);",
     "assign redirect    = ex_valid && (ex_transfers != (ex_pred_next != ex_pc + 64'd4));"),
    ("recovery goes to the predicted pc", CORE,
     "assign redirect_pc = ex_next_pc;", "assign redirect_pc = ex_pred_next;"),
    ("predictor ignores its tag", BPRED,
     "wire        btb_hit    = valid[l_entry] && (tags[l_entry] == l_tag);",
     "wire        btb_hit    = valid[l_entry];"),
    ("predictor ignores its valid bit", BPRED,
     "wire        btb_hit    = valid[l_entry] && (tags[l_entry] == l_tag);",
     "wire        btb_hit    = (tags[l_entry] == l_tag);"),
    ("prediction flips after one miss", BPRED,
     "wire        btb_taken  = btb_hit && l_count[COUNTER_BITS-1];",
     "wire        btb_taken  = btb_hit && (l_count == COUNT_MAX);"),
    ("counter never counts down", BPRED,
     "if (u_count[i] != '0) counter[u_entry[i]] <= u_count[i] - 1'b1;", ";"),
    ("counter wraps instead of saturating", BPRED,
     "if (u_count[i] != COUNT_MAX) counter[u_entry[i]] <= u_count[i] + 1'b1;",
     "counter[u_entry[i]] <= u_count[i] + 1'b1;"),
    ("target not refreshed when taken", BPRED,
     "targets[u_entry[i]] <= u_target[i];",
     "if (!u_hit[i]) targets[u_entry[i]] <= u_target[i];"),
    ("second lookup port answers for the first pc", BPRED,
     "wire [63:0] l_pc    = (i == 0) ? pc : pc2;", "wire [63:0] l_pc    = pc;"),
    ("second training port ignored", BPRED,
     "assign u_valid[i]  = (i == 0) ? upd_valid  : upd2_valid;",
     "assign u_valid[i]  = (i == 0) ? upd_valid  : 1'b0;"),
    ("older update wins on a shared entry", BPRED,
     "            for (int i = 0; i < 2; i++) begin\n                if (u_valid[i]) begin",
     "            for (int i = 1; i >= 0; i--) begin\n                if (u_valid[i]) begin"),

    ("victim still usable while replaced", CACHE,
     "                            valid[start_entry]  <= 1'b0;\n", ""),
    ("prefetch evicts the line in use", CACHE,
     "!pf_victim_busy && !st_mem && pf_victim_room;", "!st_mem && pf_victim_room;"),
    ("prefetch duplicates a present line", CACHE,
     "idle && pf_req && !pf_present && !start_miss &&",
     "idle && pf_req && !start_miss &&"),
    ("prefetch looks up the wrong set", CACHE,
     "assign pf_way_hit[w]    = valid[pf_entry] && (tags[pf_entry] == pf_tag);",
     "assign pf_way_hit[w]    = valid[entry] && (tags[entry] == pf_tag);"),
    ("prefetched line counted useful twice", CACHE,
     "                        prefetched[hit_entry] <= 1'b0;\n", ""),
    ("new line not marked recently used", CACHE,
     "                            new_age[fill_way] = 32'd0;\n", ""),
    ("hit ignored while a line is on its way", CACHE,
     "            else if (rq && hit)  ready = 1'b1;",
     "            else if (rq && hit && idle) ready = 1'b1;"),
    ("stride table ignores the instruction", PREFETCHER,
     "wire        known   = valid[entry] && (tags[entry] == acc_pc);",
     "wire        known   = valid[entry];"),
    ("stride table ignores its valid bit", PREFETCHER,
     "wire        known   = valid[entry] && (tags[entry] == acc_pc);",
     "wire        known   = (tags[entry] == acc_pc);"),
    ("prefetches after one repeat", PREFETCHER,
     "(MODE == 2) ? (acc_valid && repeats && (conf_up >= 2'd2)) :",
     "(MODE == 2) ? (acc_valid && repeats && (conf_up >= 2'd1)) :"),
    ("prefetch distance ignored", PREFETCHER,
     "wire [63:0] stride_addr = acc_addr + e_stride * DISTANCE;",
     "wire [63:0] stride_addr = acc_addr + e_stride;"),
    ("zero stride prefetched", PREFETCHER,
     "wire        repeats  = known && (delta == e_stride) && (e_stride != 64'b0);",
     "wire        repeats  = known && (delta == e_stride);"),
    ("next-line proposes the same line", PREFETCHER,
     "wire [63:0] next_line   = ((acc_addr >> OFF_BITS) + 64'd1) << OFF_BITS;",
     "wire [63:0] next_line   = (acc_addr >> OFF_BITS) << OFF_BITS;"),
    ("stride never forgotten", PREFETCHER,
     "                end else if (e_conf == 2'd0) begin\n                    stride[entry] <= delta;",
     "                end else if (e_conf == 2'd0) begin\n                    stride[entry] <= e_stride;"),

    ("load passes a store of unknown address", OOO,
     "                if (!lsq_av[p])                                         ld_blocked = 1'b1;\n"
     "                else if (st_a < ld_addr + ld_bytes && ld_addr < st_a + st_n) ld_blocked = 1'b1;",
     "                if (lsq_av[p] && st_a < ld_addr + ld_bytes && ld_addr < st_a + st_n) ld_blocked = 1'b1;"),
    ("load passes an overlapping store", OOO,
     "else if (st_a < ld_addr + ld_bytes && ld_addr < st_a + st_n) ld_blocked = 1'b1;",
     "else if (1'b0) ld_blocked = 1'b1;"),
    ("overlap judged by equal addresses only", OOO,
     "else if (st_a < ld_addr + ld_bytes && ld_addr < st_a + st_n) ld_blocked = 1'b1;",
     "else if (st_a == ld_addr) ld_blocked = 1'b1;"),
    ("load reads while the head store writes", OOO,
     "wire ld_read = ld_any && !ld_mis && !ld_blocked && !st_req;",
     "wire ld_read = ld_any && !ld_mis && !ld_blocked;"),
    ("result on the bus missed at dispatch", OOO,
     "wire          s1_on0   = cdb_valid[0] && (cdb_tag[0] == s1_tag);",
     "wire          s1_on0   = 1'b0;"),
    ("operand read from the ARF while renamed", OOO,
     "wire          s2_live  = d_uses2[s] && (rs2 != 5'd0) && rat_busy[rs2];",
     "wire          s2_live  = 1'b0;"),
    ("wake-up ignores the tag", OOO,
     "if (rs_valid[k] && !rs_r1[k] && rs_t1[k] == cdb_tag[b]) begin",
     "if (rs_valid[k] && !rs_r1[k]) begin"),
    ("wake-up keeps the old value", OOO,
     "rs_v2[k] <= cdb_value[b];", "rs_v2[k] <= rs_v2[k];"),
    ("queue entry not woken by the bus", OOO,
     "lsq_vd[k] <= cdb_value[b];", "lsq_vd[k] <= lsq_vd[k];"),
    ("RAT cleared although renamed again", OOO,
     "if (rob_wr[h] && rat_busy[h_rd] && rat_tag[h_rd] == h) rat_busy[h_rd] <= 1'b0;",
     "if (rob_wr[h]) rat_busy[h_rd] <= 1'b0;"),
    ("RAT not cleared at retirement", OOO,
     "if (rob_wr[h] && rat_busy[h_rd] && rat_tag[h_rd] == h) rat_busy[h_rd] <= 1'b0;",
     ";"),
    ("RAT survives a flush", OOO,
     "            rat_busy  <= '0;", "            rat_busy  <= rst ? '0 : rat_busy;"),
    ("reservation stations survive a flush", OOO,
     "            rs_valid  <= '0;", "            rs_valid  <= rst ? '0 : rs_valid;"),
    ("load/store queue survives a flush", OOO,
     "            lsq_valid <= '0;", "            lsq_valid <= rst ? '0 : lsq_valid;"),
    ("store written although it will trap", OOO,
     "wire        st_req   = h_valid && rob_store[h] && rob_done[h] && !rob_exc[h];",
     "wire        st_req   = h_valid && rob_store[h] && rob_done[h];"),
    ("faulting store retires", OOO,
     "wire        st_ok    = st_req && dmem_ready && !dmem_fault;",
     "wire        st_ok    = st_req && dmem_ready;"),
    ("store data not cut to its size", OOO,
     "wire [63:0] st_data  = lsq_vd[lsq_head] & st_mask;",
     "wire [63:0] st_data  = lsq_vd[lsq_head];"),
    ("retires before the result is there", OOO,
     "wire plain_fire = h_valid && rob_done[h] && !rob_exc[h] && !rob_store[h] && !rob_sys[h];",
     "wire plain_fire = h_valid && !rob_exc[h] && !rob_store[h] && !rob_sys[h];"),
    ("misprediction ignored at retirement", OOO,
     "wire h_mis      = retire && rob_mis[h] && !do_mret;", "wire h_mis      = 1'b0;"),
    ("ALU result lost when a load has the bus", OOO,       # a width-1 bug
     "assign x_fire[0] = sel_any[0] && ((WIDTH == 2) || !ld_done);",
     "assign x_fire[0] = sel_any[0];"),
    ("system instruction enters a busy ROB", OOO,
     "                || (d_sys[0] && !rob_empty)             // wait for the ROB to drain\n", ""),
    ("dispatch behind a system instruction", OOO,
     "                || (!rob_empty && rob_sys[rob_head]);   // nothing follows it in",
     "                ;"),
    ("exception on the bus not recorded", OOO,
     "rob_exc[cdb_tag[b]]   <= 1'b1;", "rob_exc[cdb_tag[b]]   <= 1'b0;"),
    ("misaligned store does not trap", OOO,
     "                if (lsq_mis[st_idx]) begin\n                    rob_exc[lsq_tag[st_idx]]   <= 1'b1;",
     "                if (lsq_mis[st_idx]) begin\n                    rob_exc[lsq_tag[st_idx]]   <= 1'b0;"),
    ("wrong next pc recorded for a branch", OOO,
     "rob_next[x_tag[u]]   <= x_next[u];", "rob_next[x_tag[u]]   <= x_target[u];"),
    ("non-branch misprediction not noticed", OOO,
     "rob_mis[dp_t[s]]    <= !d_exec[s] && (fd_pred[s] != fd_pc[s] + 64'd4);",
     "rob_mis[dp_t[s]]    <= 1'b0;"),
    ("taken branch continues at pc + 4", EXEC,
     "assign next      = transfers ? target : pc + 64'd4;",
     "assign next      = pc + 64'd4;"),
    ("misprediction not detected", EXEC,
     "assign mis       = !misalign && (next != pred);", "assign mis       = 1'b0;"),
    ("misaligned jump target not an exception", EXEC,
     "assign misalign  = taken && target[1];", "assign misalign  = 1'b0;"),

    ("second of a pair misses the first one's result (rs1)", OOO,
     "wire s1_pair = (s == 1) && d_writes[0] && d_uses1[s] && (d_rd[0] == rs1);",
     "wire s1_pair = 1'b0;"),
    ("second of a pair misses the first one's result (rs2)", OOO,
     "wire s2_pair = (s == 1) && d_writes[0] && d_uses2[s] && (d_rd[0] == rs2);",
     "wire s2_pair = 1'b0;"),
    ("pair dependency on a first that writes nothing", OOO,
     "wire s1_pair = (s == 1) && d_writes[0] && d_uses1[s] && (d_rd[0] == rs1);",
     "wire s1_pair = (s == 1) && d_uses1[s] && (d_rd[0] == rs1);"),
    ("first of two writers keeps the RAT", OOO,
     "                    if (d_writes[s]) begin\n",
     "                    if (d_writes[s] && !(s == 1 && d_writes[0] && d_rd[0] == d_rd[1])) begin\n"),
    ("result on the second bus missed at dispatch", OOO,
     "wire          s2_on1   = cdb_valid[1] && (cdb_tag[1] == s2_tag);",
     "wire          s2_on1   = 1'b0;"),
    ("second bus wakes nothing up", OOO,
     "            for (int b = 0; b < 2; b++) begin\n                if (cdb_valid[b]) begin\n"
     "                    rob_done[cdb_tag[b]]  <= 1'b1;",
     "            for (int b = 0; b < 1; b++) begin\n                if (cdb_valid[b]) begin\n"
     "                    rob_done[cdb_tag[b]]  <= 1'b1;"),
    ("three results for two buses", OOO,
     "assign x_fire[1] = sel_any[1] && (WIDTH == 2) && !ld_done;",
     "assign x_fire[1] = sel_any[1] && (WIDTH == 2);"),
    ("second bus carries the wrong unit's tag", OOO,
     "assign cdb_tag[1]   = ld_done ? x_tag[0]      : x_tag[1];",
     "assign cdb_tag[1]   = x_tag[1];"),
    ("second bus carries the wrong unit's value", OOO,
     "assign cdb_value[1] = ld_done ? x_value[0]    : x_value[1];",
     "assign cdb_value[1] = x_value[1];"),
    ("both units execute the same station", OOO,
     "if (rs_valid[k] && rs_r1[k] && rs_r2[k] && !(any0 && sel0 == k)) begin",
     "if (rs_valid[k] && rs_r1[k] && rs_r2[k]) begin"),
    ("second unit's station not freed", OOO,
     "            for (int u = 0; u < 2; u++) begin\n                if (x_fire[u]) begin\n"
     "                    rs_valid[sel[u]]     <= 1'b0;",
     "            for (int u = 0; u < 2; u++) begin\n                if (x_fire[u]) begin\n"
     "                    rs_valid[sel[0]]     <= 1'b0;"),
    ("second of a pair takes the first one's station", OOO,
     "assign dp_rs[1] = d_exec[0] ? rs_free1 : rs_free0;", "assign dp_rs[1] = rs_free0;"),
    ("second of a pair takes the first one's queue entry", OOO,
     "assign dp_l[1]  = d_mem[0] ? lsq_tail + 1'b1 : lsq_tail;", "assign dp_l[1]  = lsq_tail;"),
    ("pair dispatched into one free ROB entry", OOO,
     "assign d_block[1] = (WIDTH != 2) || !rob_two", "assign d_block[1] = (WIDTH != 2)"),
    ("pair dispatched into one free station", OOO,
     "|| (d_exec[1] && !(d_exec[0] ? rs_any1 : rs_any0))", "|| (d_exec[1] && !rs_any0)"),
    ("pair dispatched into one free queue entry", OOO,
     "|| (d_mem[1] && (lsq_count + d_mem[0] >= LSQ_ENTRIES));",
     "|| (d_mem[1] && lsq_full);"),
    ("instruction dispatched beside a system instruction", OOO,
     "                || d_sys[0] || d_sys[1]\n", "                || d_sys[1]\n"),
    ("system instruction dispatched as the second of a pair", OOO,
     "                || d_sys[0] || d_sys[1]\n", "                || d_sys[0]\n"),
    ("second fetched although the first is predicted taken", OOO,
     "wire        if_two   = (WIDTH == 2) && imem_valid2 && !p0_taken;",
     "wire        if_two   = (WIDTH == 2) && imem_valid2;"),
    ("fetch continues after the first of a pair", OOO,
     "pc_q        <= if_two ? if_next1 : if_next0;", "pc_q        <= if_next0;"),
    ("second of a pair lost when the first goes alone", OOO,
     "                fd_valid    <= 2'b01;", "                fd_valid    <= 2'b00;"),
    ("second of a pair not moved up", OOO,
     "                fd_instr[0] <= fd_instr[1];\n", ""),
    ("second fetched across a line boundary", MEMSYS,
     "assign imem_valid2 = i_in_ram && (i_offset <= LINE_BYTES - 8);",
     "assign imem_valid2 = i_in_ram;"),
    ("second retires although the first redirects fetch", OOO,
     "wire retire2    = (WIDTH == 2) && retire && !h_mis && !do_mret && (rob_count >= 2)",
     "wire retire2    = (WIDTH == 2) && retire && !do_mret && (rob_count >= 2)"),
    ("second retires from an empty entry", OOO,
     "wire retire2    = (WIDTH == 2) && retire && !h_mis && !do_mret && (rob_count >= 2)",
     "wire retire2    = (WIDTH == 2) && retire && !h_mis && !do_mret"),
    ("second retires before its result is there", OOO,
     "&& rob_done[h2] && !rob_exc[h2] && !rob_store[h2] && !rob_sys[h2];",
     "&& !rob_exc[h2] && !rob_store[h2] && !rob_sys[h2];"),
    ("second retires although it raises an exception", OOO,
     "&& rob_done[h2] && !rob_exc[h2] && !rob_store[h2] && !rob_sys[h2];",
     "&& rob_done[h2] && !rob_store[h2] && !rob_sys[h2];"),
    ("store retires as the second of a pair", OOO,
     "&& rob_done[h2] && !rob_exc[h2] && !rob_store[h2] && !rob_sys[h2];",
     "&& rob_done[h2] && !rob_exc[h2] && !rob_sys[h2];"),
    ("misprediction by the second of a pair ignored", OOO,
     "wire h2_mis     = retire2 && rob_mis[h2];", "wire h2_mis     = 1'b0;"),
    ("recovery from the second uses the first one's next pc", OOO,
     "assign flush_pc = trap ? mtvec : do_mret ? mepc : h_mis ? rob_next[h] : rob_next[h2];",
     "assign flush_pc = trap ? mtvec : do_mret ? mepc : rob_next[h];"),
    ("older result stays when a pair writes one register", OOO,
     "if (arf2_we) arf[h2_rd] <= rob_value[h2];",
     "if (arf2_we && !(arf_we && h_rd == h2_rd)) arf[h2_rd] <= rob_value[h2];"),
    ("RAT not cleared when the second of a pair retires", OOO,
     "if (rob_wr[h2] && rat_busy[h2_rd] && rat_tag[h2_rd] == h2) rat_busy[h2_rd] <= 1'b0;",
     ";"),
    ("RAT cleared by the second although renamed again", OOO,
     "if (rob_wr[h2] && rat_busy[h2_rd] && rat_tag[h2_rd] == h2) rat_busy[h2_rd] <= 1'b0;",
     "if (rob_wr[h2]) rat_busy[h2_rd] <= 1'b0;"),
    ("one queue entry freed when two retire", OOO,
     "if (pop_a && pop_b)  lsq_valid[lsq_second] <= 1'b0;", ";"),
    ("load retiring second not taken off the queue", OOO,
     "wire pop_b = retire2 && rob_load[h2];", "wire pop_b = 1'b0;"),
    ("second retirement not counted in minstret", CSR,
     "minstret_q <= minstret_q + {63'b0, retire} + {63'b0, retire2};",
     "minstret_q <= minstret_q + {63'b0, retire};", "ss"),

    # --- cache policies (Phase 8) ---
    # Bugs that lose or corrupt data: a trace or the final memory must show
    # them.
    ("write-through store not sent to memory", CACHE,
     "wire st_thru   = rq && we && hit && (WRITE_HIT != 0);",
     "wire st_thru   = 1'b0;"),
    ("store goes round a line that is on its way", CACHE,
     "wire st_around = rq && we && !hit && (WRITE_MISS != 0) && !on_way && !vb_hit;",
     "wire st_around = rq && we && !hit && (WRITE_MISS != 0) && !vb_hit;"),
    ("read overtakes a queued write to its line", CACHE,
     "wire rf_issue    = (state == S_RF_REQ) && !drain_wait && !rf_conflict;",
     "wire rf_issue    = (state == S_RF_REQ) && !drain_wait;"),
    ("store accepted by a full write buffer", CACHE,
     "wire push_ok = merge_found || wb_room;", "wire push_ok = 1'b1;"),
    ("store finished although the buffer did not take it", CACHE,
     "else if (st_mem)     ready = HAS_WBUF ? push_ok : dw_done;",
     "else if (st_mem)     ready = HAS_WBUF ? 1'b1 : dw_done;"),
    ("store merged into a write already on its way", CACHE,
     "                        !(mg_pos == 32'd0 && head_locked) &&\n", ""),
    ("merge drops the bytes queued before", CACHE,
     "wb_strb[merge_idx] <= wb_strb[merge_idx] | wr_strobe;",
     "wb_strb[merge_idx] <= wr_strobe;"),
    ("merge into a write to another line", CACHE,
     "if (mg_pos < wb_count && wb_addr[k] == line_addr &&",
     "if (mg_pos < wb_count &&"),
    ("dirty victim queued without its bytes", CACHE,
     "                    wb_strb[wb_tail] <= '1;", "                    wb_strb[wb_tail] <= '0;"),
    ("dirty victim queued under the new line's address", CACHE,
     "wb_addr[wb_tail] <= out_addr;",
     "wb_addr[wb_tail] <= start_miss ? line_addr : pf_line_addr;"),
    ("store done before memory has answered", CACHE,
     "wire dw_done   = !HAS_WBUF && mem_resp && (dw_issue || (dw_wait && req));",
     "wire dw_done   = !HAS_WBUF && (dw_issue || (dw_wait && req));"),
    ("prefetch starts while a store uses the write buffer", CACHE,
     "!pf_victim_busy && !st_mem && pf_victim_room;", "!pf_victim_busy && pf_victim_room;"),
    ("miss starts without room for its dirty victim", CACHE,
     "wire victim_room    = !HAS_WBUF || wb_room || !victim_writes;",
     "wire victim_room    = 1'b1;"),
    ("queued write freed before memory has answered", CACHE,
     "wire drain_resp  = mem_resp && (drain_wait || drain_issue);",
     "wire drain_resp  = drain_wait || (mem_resp && drain_issue);"),
    # Bugs that leave every result right: only the cache model can show them.
    ("FIFO order disturbed by a hit", CACHE,
     "                    if (REPL == 0) begin\n                        for (int k = 0; k < WAYS; k++)\n"
     "                            if (age[set * WAYS + k] < age[hit_entry])",
     "                    if (REPL <= 1) begin\n                        for (int k = 0; k < WAYS; k++)\n"
     "                            if (age[set * WAYS + k] < age[hit_entry])"),
    ("LRU order not updated by a hit", CACHE,
     "                        age[hit_entry] <= 32'd0;\n", ""),
    ("hit forgotten when a line arrives in the same set", CACHE,
     "wire         hit_in_fill_set = acc_hit && (fill_set == set);",
     "wire         hit_in_fill_set = 1'b0;"),
    ("random replacement ignores empty ways", CACHE,
     "            if (!way_valid[k])    victim_way    = k;\n"
     "            if (!pf_way_valid[k]) pf_victim_way = k;\n", ""),
    ("random replacement never changes its choice", CACHE,
     "fill_is_prefetch <= !start_miss;\n"
     "                            lfsr <= {lfsr[14:0], lfsr[15] ^ lfsr[13] ^ lfsr[12] ^ lfsr[10]};",
     "fill_is_prefetch <= !start_miss;\n"
     "                            lfsr <= lfsr;"),
    ("pseudo-LRU tree not updated by a hit", CACHE,
     "if (REPL == 3) plru[set] <= plru_touch(set_plru, hit_way);", ";"),
    ("pseudo-LRU points at the line just used", CACHE,
     "r[node >> 1] = ~node[0];", "r[node >> 1] = node[0];"),
    ("pseudo-LRU ignores the tree below the root", CACHE,
     "node    = (node << 1) | {31'b0, set_plru[node]};",
     "node    = (node << 1) | {31'b0, set_plru[1]};"),
    ("line brought in for a store although stores go round", CACHE,
     "idle && rq && !hit && !vb_hit &&\n                       !(we && (WRITE_MISS != 0));",
     "idle && rq && !hit && !vb_hit;"),
    ("line marked dirty although written through", CACHE,
     "if (WRITE_HIT == 0) dirty[hit_entry] <= 1'b1;", "dirty[hit_entry] <= 1'b1;"),
    ("clean victim written back", CACHE,
     "wire start_dirty  = start_evicts && dirty[start_entry];",
     "wire start_dirty  = start_evicts;"),
    ("write buffer does not merge", CACHE,
     "                        merge_found = 1'b1;\n", ""),
]


HIER_MUTATIONS = [
    # The line port and the way between the levels.
    ("line write ignores its strobes", CACHE,
     "assign wr_bits[8*b +: 8] = {8{lwstrb[b]}};", "assign wr_bits[8*b +: 8] = 8'hFF;"),
    ("part of a line passed on as a whole line", CACHE,
     "assign wr_strobe = lwstrb;", "assign wr_strobe = '1;"),
    ("strobes of a write lost between the levels", ARBITER,
     "assign dn_wstrb = b_wstrb_q;", "assign dn_wstrb = b_wstrb;"),
    ("request passed down with a later address", ARBITER,
     "assign dn_addr  = pick_b ? b_addr_q : a_addr_q;",
     "assign dn_addr  = pick_b ? b_addr : a_addr_q;"),
    ("request of the instruction cache forgotten", ARBITER,
     "                a_held   <= 1'b1;", "                a_held   <= 1'b0;"),
    ("arbiter answers the wrong cache", ARBITER,
     "assign a_resp  = serve && !pick_b && dn_ready;\n"
     "    assign b_resp  = serve && pick_b && dn_ready;",
     "assign a_resp  = serve && pick_b && dn_ready;\n"
     "    assign b_resp  = serve && !pick_b && dn_ready;"),
    ("write passed down as a read", ARBITER,
     "assign dn_we    = pick_b && b_we_q;", "assign dn_we    = pick_b && b_we;"),
    ("request changed under the L2 while it is being served", ARBITER,
     "wire pick_b = busy ? cur_b : (a_held && b_held) ? !last_b : b_held;",
     "wire pick_b = (a_held && b_held) ? !last_b : b_held;"),
    ("second-level hit latency ignored", MEMSYS,
     ".HIT_LATENCY  (L2_HIT_LATENCY - 1),", ".HIT_LATENCY  (0),"),
    # Inclusion: the cache that takes lines back.
    ("line evicted without being taken back", CACHE,
     "wire miss_recall = (INCLUSION != 0) && miss_wanted && valid[victim_entry] &&",
     "wire miss_recall = 1'b0 && miss_wanted && valid[victim_entry] &&"),
    ("wrong line taken back", CACHE,
     "                                ? ((tags[victim_entry] << IDX_BITS) | {32'b0, set}) << OFF_BITS",
     "                                ? line_addr"),
    ("taking a line back counts for later evictions too", CACHE,
     "                recalled <= 1'b0;\n", ""),
    ("dirty copy from above not written into the line", CACHE,
     "                            lines[rc_entry] <= rc_line;\n", ""),
    ("line taken back dirty stays clean", CACHE,
     "                            dirty[rc_entry] <= 1'b1;\n", ""),
    ("dirty copy from the data cache not passed down", MEMSYS,
     "wire                 up_dirty = ic_inv_dirty || dc_inv_dirty;",
     "wire                 up_dirty = ic_inv_dirty;"),
    ("data cache never told to give a line up", MEMSYS,
     ".inv_req       (inv_req),\n        .inv_addr      (inv_addr),\n"
     "        .inv_dirty     (dc_inv_dirty),",
     ".inv_req       (1'b0),\n        .inv_addr      (inv_addr),\n"
     "        .inv_dirty     (dc_inv_dirty),"),
    # Inclusion: the cache that gives lines up.
    ("line not given up when asked", CACHE,
     "                        valid[inv_entry]      <= 1'b0;\n", ""),
    ("dirty line given up as clean", CACHE,
     "assign inv_dirty = inv_take && (inv_vb_hit ? vb_dirty[inv_vb_idx] : dirty[inv_entry]);",
     "assign inv_dirty = 1'b0;"),
    ("line given up with another line's content", CACHE,
     "assign inv_line  = inv_vb_hit ? vb_line[inv_vb_idx] : lines[inv_entry];",
     "assign inv_line  = inv_vb_hit ? vb_line[inv_vb_idx] : lines[hit_entry];"),
    ("line to give up looked for in the wrong set", CACHE,
     "assign inv_way_hit[w]   = valid[inv_way] && (tags[inv_way] == inv_tag);",
     "assign inv_way_hit[w]   = valid[entry] && (tags[entry] == inv_tag);"),
    ("access served in the cycle its line is given up", CACHE,
     "wire  rq = req && lat_ok && !inv_take && (state != S_INV);",
     "wire  rq = req && lat_ok && (state != S_INV);"),
    ("prefetch starts in the cycle a line is given up", CACHE,
     "wire pf_clear   = pf_wanted && !swap_start && !miss_recall && !inv_take;",
     "wire pf_clear   = pf_wanted && !swap_start && !miss_recall;"),
    ("victim buffer not searched for the line to give up", CACHE,
     "if (vb_valid[k] && vb_addr[k] == inv_line_addr) begin",
     "if (1'b0 && vb_addr[k] == inv_line_addr) begin"),
    ("store in flight not finished when its line is given up", CACHE,
     "wire dw_done   = !HAS_WBUF && mem_resp && (dw_issue || (dw_wait && req));",
     "wire dw_done   = !HAS_WBUF && mem_resp && (dw_issue || (dw_wait && st_mem));"),
    # Victim buffer.
    ("victim not put into the victim buffer", CACHE,
     "                                vb_valid[vb_slot] <= 1'b1;\n", ""),
    ("victim buffer forgets that a line is dirty", CACHE,
     "vb_dirty[vb_slot] <= dirty[start_entry];", "vb_dirty[vb_slot] <= 1'b0;"),
    ("line comes out of the victim buffer clean", CACHE,
     "dirty[victim_entry]      <= vb_dirty[vb_idx];", "dirty[victim_entry]      <= 1'b0;"),
    ("swap leaves the victim's data behind", CACHE,
     "                                vb_line[vb_idx]  <= lines[victim_entry];\n", ""),
    ("victim buffer answers for any address", CACHE,
     "if (vb_valid[k] && vb_addr[k] == line_addr) begin", "if (vb_valid[k]) begin"),
    ("dirty line pushed out of the victim buffer not written back", CACHE,
     "wire                 out_dirty  = HAS_VB ? (start_evicts && vb_out_dirty) : start_dirty;",
     "wire                 out_dirty  = HAS_VB ? 1'b0 : start_dirty;"),
    ("prefetch fetches a line the victim buffer holds", CACHE,
     "wire                 pf_present   = (|pf_way_hit) || pf_vb_hit;",
     "wire                 pf_present   = (|pf_way_hit);"),
    ("store goes round a line the victim buffer holds", CACHE,
     "wire st_around = rq && we && !hit && (WRITE_MISS != 0) && !on_way && !vb_hit;",
     "wire st_around = rq && we && !hit && (WRITE_MISS != 0) && !on_way;"),
    ("victim buffer always replaces the same entry", CACHE,
     "if (vb_valid[vb_slot]) vb_next <= (vb_next + 32'd1) % VB_N;", ";"),
    ("line in the victim buffer fetched from memory instead", CACHE,
     "wire swap_start = HAS_VB && (ENABLE != 0) && idle && rq && !hit && vb_hit;",
     "wire swap_start = 1'b0;"),
]
MUTATIONS += HIER_MUTATIONS


class RegressionCrashed(Exception):
    pass


def regress(stage, *selection):
    proc = subprocess.run([sys.executable, "tools/regress.py", stage, *selection],
                          cwd=ROOT, capture_output=True, text=True)
    # The regression always ends with a line of totals.
    lines = proc.stdout.strip().splitlines()
    if not lines or " passed" not in lines[-1]:
        raise RegressionCrashed(f"tools/regress.py {stage} {' '.join(selection)}\n"
                                + proc.stderr[-2000:])
    return proc.stdout


def failures(output):
    return sum(line.startswith("  FAIL") for line in output.splitlines())


def seen_by(output):
    """Which of the regression's checks failed: trace, memory, model."""
    kinds = set()
    for line in output.splitlines():
        if not line.startswith("  FAIL"):
            continue
        if "ERROR" in line:
            kinds.add("invariant")
        elif "cache log" in line or "counter" in line or "event log" in line:
            kinds.add("model")
        elif "final memory" in line:
            kinds.add("memory")
        else:
            kinds.add("trace")
    return kinds


def backup_path(relpath):
    return os.path.join(BACKUP_DIR, relpath.replace("/", "__"))


def restore_interrupted():
    """Put back any file a previous, interrupted run left mutated."""
    if not os.path.isdir(BACKUP_DIR):
        return
    for name in os.listdir(BACKUP_DIR):
        relpath = name.replace("__", "/")
        shutil.copyfile(os.path.join(BACKUP_DIR, name), os.path.join(ROOT, relpath))
        os.remove(os.path.join(BACKUP_DIR, name))
        print(f"restored {relpath} from an interrupted run", flush=True)


def check_clean():
    """True if no planted bug is present in any RTL file."""
    dirty = []
    for name, relpath, old in (m[:3] for m in MUTATIONS):
        with open(os.path.join(ROOT, relpath), encoding="utf-8") as handle:
            if handle.read().count(old) != 1:
                dirty.append((name, relpath))
    for name, relpath in dirty:
        print(f"NOT CLEAN  {relpath}: original text of '{name}' is missing")
    if not dirty:
        print(f"clean: the original text of all {len(MUTATIONS)} mutations is present")
    return not dirty


def main():
    restore_interrupted()
    if "--check" in sys.argv[1:]:
        return 0 if check_clean() else 1
    # An optional argument selects the mutations whose description contains it.
    # or, with --file, those planted in a file whose path contains it.
    names = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    wanted = names[0] if names else ""
    if "--hier" in sys.argv[1:]:
        selected = [m for m in HIER_MUTATIONS if wanted in m[0]]
    elif "--file" in sys.argv[1:]:
        selected = [m for m in MUTATIONS if wanted in m[1]]
    else:
        selected = [m for m in MUTATIONS if wanted in m[0]]
    os.makedirs(BACKUP_DIR, exist_ok=True)
    caught = 0
    for mutation in selected:
        name, relpath, old, new = mutation[:4]
        path = os.path.join(ROOT, relpath)
        with open(path, encoding="utf-8") as handle:
            original = handle.read()
        if original.count(old) != 1:
            print(f"STALE   {name:<36} pattern not found in {relpath}", flush=True)
            continue
        blocks = ""
        shutil.copyfile(path, backup_path(relpath))
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(original.replace(old, new))
            started = time.time()
            configs = configs_for(mutation)
            for core in cores_for(mutation):
                directed = regress("rtl", "-v", "--core", core, "-c", configs,
                                   "-k", "tests/")
                t_directed = time.time() - started
                random_out = regress("rtl", "-v", "--core", core, "-c", configs,
                                     "-k", "seed", "--seeds", str(SEEDS))
                if failures(directed) or failures(random_out):
                    break
            blocks = "".join(regress("blocks", "-k", prefix + ":")
                             for prefix in blocks_for(mutation))
            crashed = None
        except RegressionCrashed as crash:
            crashed = crash
        finally:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(original)
            os.remove(backup_path(relpath))
        if crashed:
            print(f"INVALID the regression script itself crashed on '{name}', so the "
                  f"result would mean nothing; fix the script first.\n{crashed}", flush=True)
            return 2
        if "compile error" in directed + random_out + blocks:
            print(f"BROKEN  {name:<36} mutated RTL does not compile", flush=True)
            continue
        # A program that does not assemble, or fails on the golden model, fails
        # whatever the RTL does and would make every bug look caught.
        if any(text in directed + random_out for text in
               ("assembly error", "golden model timeout", "golden model exit code")):
            print("INVALID a test program fails by itself (assembly or golden model); "
                  "fix the tests before running this check", flush=True)
            return 2
        counts = (failures(directed), failures(random_out), failures(blocks))
        caught += any(counts)
        detail = (f"directed runs failing: {counts[0]}, "
                  f"random programs failing: {counts[1]}/{SEEDS}")
        if len(cores_for(mutation)) > 1:
            detail += f" (on {core})"
        if "--time" in sys.argv:
            detail += f" [{t_directed:.0f}s directed, {time.time() - started:.0f}s total]"
        if blocks_for(mutation):
            detail += f", block testbenches failing: {counts[2]}"
        kinds = seen_by(directed + random_out)
        if kinds:
            detail += " [" + ", ".join(sorted(kinds)) + "]"
        print(f"{'CAUGHT' if any(counts) else 'MISSED'}  {name:<36} {detail}", flush=True)
    print(f"\n{caught}/{len(selected)} mutations caught")
    return 0 if caught == len(selected) else 1


if __name__ == "__main__":
    sys.exit(main())
