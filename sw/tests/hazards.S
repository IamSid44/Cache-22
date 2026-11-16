# hazards.S - pipeline hazards, one check per forwarding or stall path.
.equ HALT, 0x10000008

    la   s0, data               # data[0] = 100, data[1] = 7, data[2] = &data[1]

    li   gp, 1                  # distance 1, rs1: forward from MEM
    li   t0, 5
    addi t1, t0, 1
    addi t2, t1, 1
    li   t3, 7
    bne  t2, t3, fail

    li   gp, 2                  # distance 1, rs2
    li   t0, 5
    addi t1, t0, 1
    sub  t2, t0, t1
    li   t3, -1
    bne  t2, t3, fail

    li   gp, 3                  # distance 2: forward from WB
    li   t0, 9
    addi t1, t0, 1
    nop
    add  t2, t1, t1
    li   t3, 20
    bne  t2, t3, fail

    li   gp, 4                  # distance 3: register-file bypass
    addi t1, t0, 2
    nop
    nop
    add  t2, t1, x0
    li   t3, 11
    bne  t2, t3, fail

    li   gp, 5                  # two producers: the younger one must win
    li   t1, 1
    li   t1, 2
    add  t2, t1, t1
    li   t3, 4
    bne  t2, t3, fail

    li   gp, 6                  # a write to x0 must never be forwarded
    addi x0, x0, 99
    add  t2, x0, x0
    bnez t2, fail

    li   gp, 7                  # load-use, loaded value as rs1
    ld   t1, 0(s0)
    addi t2, t1, 1
    li   t3, 101
    bne  t2, t3, fail

    li   gp, 8                  # load-use, loaded value as rs2
    li   t0, 1000
    ld   t1, 0(s0)
    sub  t2, t0, t1
    li   t3, 900
    bne  t2, t3, fail

    li   gp, 9                  # load-use, loaded value as both operands
    ld   t1, 8(s0)
    add  t2, t1, t1
    li   t3, 14
    bne  t2, t3, fail

    li   gp, 10                 # load then store of the loaded value
    ld   t1, 0(s0)
    sd   t1, 24(s0)
    ld   t2, 24(s0)
    li   t3, 100
    bne  t2, t3, fail

    li   gp, 11                 # load then load through the loaded pointer
    ld   t1, 16(s0)
    ld   t2, 0(t1)
    li   t3, 7
    bne  t2, t3, fail

    li   gp, 12                 # load then store through the loaded pointer
    ld   t1, 16(s0)
    sd   t3, 24(t1)
    ld   t2, 32(s0)
    bne  t2, t3, fail

    li   gp, 13                 # load then branch on it, as rs1 and as rs2
    li   t3, 100
    ld   t1, 0(s0)
    bne  t1, t3, fail
    ld   t1, 0(s0)
    bne  t3, t1, fail

    li   gp, 14                 # load then jalr through the loaded address
    la   t0, landing
    sd   t0, 40(s0)
    ld   t1, 40(s0)
    jalr t2, 0(t1)
    j    fail
landing:

    li   gp, 15                 # store then load of the same address
    li   t0, 0x5a5a
    sd   t0, 48(s0)
    ld   t1, 48(s0)
    bne  t1, t0, fail

    li   gp, 16                 # instructions behind a taken branch are squashed
    li   t0, 1
    li   t1, 0
    beq  t0, t0, taken
    addi t1, t1, 1              # must not execute
    sd   t0, 56(s0)             # must not execute
taken:
    bnez t1, fail
    ld   t2, 56(s0)
    bnez t2, fail

    li   gp, 17                 # branch whose operand is produced one cycle before
    li   t0, 3
    addi t1, t0, -3
    bnez t1, fail
    addi t1, t0, -2
    beqz t1, fail

    li   gp, 18                 # a squashed load must not cause a stall-induced skip
    li   t2, 5
    j    over
    ld   t2, 0(s0)              # squashed
    addi t2, t2, 1              # squashed
over:
    addi t2, t2, 1
    li   t3, 6
    bne  t2, t3, fail

pass:
    li   t6, HALT
    sd   x0, 0(t6)
pass_loop:
    j    pass_loop
fail:
    li   t6, HALT
    sd   gp, 0(t6)
fail_loop:
    j    fail_loop

    .align 3
data:
    .dword 100
    .dword 7
    .dword data + 8
    .dword 0, 0, 0, 0, 0
