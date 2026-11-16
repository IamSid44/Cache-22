# arith.S - integer arithmetic, logic, comparisons, lui/auipc, 32-bit (W) ops.
# Self-checking: gp holds the number of the check in progress; on a mismatch it
# is written to the HALT register as the exit code. Exit code 0 = pass.
.equ HALT, 0x10000008

    li   gp, 1                  # add
    li   t0, 5
    li   t1, 7
    add  t2, t0, t1
    li   t3, 12
    bne  t2, t3, fail

    li   gp, 2                  # sub, negative result
    sub  t2, t0, t1
    li   t3, -2
    bne  t2, t3, fail

    li   gp, 3                  # 64-bit wrap-around
    li   t0, 0x7fffffffffffffff
    addi t2, t0, 1
    li   t3, 0x8000000000000000
    bne  t2, t3, fail

    li   gp, 4                  # and / or / xor
    li   t0, 0xff00ff00
    li   t1, 0x0ff00ff0
    and  t2, t0, t1
    li   t3, 0x0f000f00
    bne  t2, t3, fail
    li   gp, 5
    or   t2, t0, t1
    li   t3, 0xfff0fff0
    bne  t2, t3, fail
    li   gp, 6
    xor  t2, t0, t1
    li   t3, 0xf0f0f0f0
    bne  t2, t3, fail

    li   gp, 7                  # slt is signed, sltu is unsigned
    li   t0, -1
    li   t1, 1
    slt  t2, t0, t1
    bne  t2, t1, fail
    li   gp, 8
    sltu t2, t0, t1
    bnez t2, fail
    li   gp, 9
    sltu t2, t1, t0
    bne  t2, t1, fail

    li   gp, 10                 # slti / sltiu
    li   t0, -5
    slti t2, t0, -4
    li   t3, 1
    bne  t2, t3, fail
    li   gp, 11
    slti t2, t0, -5
    bnez t2, fail
    li   gp, 12                 # sltiu sign-extends its immediate first
    li   t0, 5
    sltiu t2, t0, -1
    bne  t2, t3, fail

    li   gp, 13                 # andi / ori / xori
    li   t0, 0x123
    andi t2, t0, 0x0f0
    li   t3, 0x020
    bne  t2, t3, fail
    li   gp, 14
    ori  t2, t0, 0x0f0
    li   t3, 0x1f3
    bne  t2, t3, fail
    li   gp, 15
    xori t2, t0, -1
    li   t3, -0x124
    bne  t2, t3, fail

    li   gp, 16                 # x0 is hard-wired to zero
    addi x0, x0, 5
    mv   t2, x0
    bnez t2, fail

    li   gp, 17                 # lui sign-extends from bit 31
    lui  t2, 0x80000
    li   t3, -0x80000000
    bne  t2, t3, fail

    li   gp, 18                 # auipc
here:
    auipc t2, 0
    la   t3, here
    bne  t2, t3, fail
    li   gp, 19
here2:
    auipc t2, 1
    la   t3, here2
    li   t4, 0x1000
    add  t3, t3, t4
    bne  t2, t3, fail

    li   gp, 20                 # 64-bit constant, checked half by half
    li   t0, 0x123456789abcdef0
    srli t2, t0, 32
    li   t3, 0x12345678
    bne  t2, t3, fail
    li   gp, 21
    slli t2, t0, 32
    srli t2, t2, 32
    li   t3, 0x9abcdef0
    bne  t2, t3, fail

    li   gp, 22                 # addw wraps at 32 bits and sign-extends
    li   t0, 0x7fffffff
    li   t1, 1
    addw t2, t0, t1
    li   t3, -0x80000000
    bne  t2, t3, fail
    li   gp, 23                 # add on the same operands does not wrap
    add  t2, t0, t1
    li   t3, 0x80000000
    bne  t2, t3, fail

    li   gp, 24                 # subw
    subw t2, x0, t1
    li   t3, -1
    bne  t2, t3, fail

    li   gp, 25                 # addiw ignores the upper 32 bits
    li   t0, 0x100000000
    addiw t2, t0, 0
    bnez t2, fail
    li   gp, 26
    li   t0, 0xffffffff
    addiw t2, t0, 0
    li   t3, -1
    bne  t2, t3, fail
    li   gp, 27
    addiw t2, t0, 2
    li   t3, 1
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
