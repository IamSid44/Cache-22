# shifts.S - register and immediate shifts, 64-bit and 32-bit (W) forms.
# Self-checking: exit code 0 = pass, otherwise the number of the failed check.
.equ HALT, 0x10000008

    li   gp, 1                  # sll
    li   t0, 1
    li   t1, 63
    sll  t2, t0, t1
    li   t3, 0x8000000000000000
    bne  t2, t3, fail

    li   gp, 2                  # only the low 6 bits of the amount are used
    li   t1, 64
    sll  t2, t0, t1
    bne  t2, t0, fail

    li   gp, 3                  # srl shifts in zeros
    li   t0, -1
    li   t1, 60
    srl  t2, t0, t1
    li   t3, 0xf
    bne  t2, t3, fail

    li   gp, 4                  # sra shifts in the sign
    sra  t2, t0, t1
    bne  t2, t0, fail
    li   gp, 5
    li   t0, 0x4000000000000000
    li   t1, 62
    sra  t2, t0, t1
    li   t3, 1
    bne  t2, t3, fail

    li   gp, 6                  # slli
    li   t0, 1
    slli t2, t0, 40
    li   t3, 0x10000000000
    bne  t2, t3, fail

    li   gp, 7                  # srli
    li   t0, -1
    srli t2, t0, 1
    li   t3, 0x7fffffffffffffff
    bne  t2, t3, fail

    li   gp, 8                  # srai
    li   t0, -16
    srai t2, t0, 2
    li   t3, -4
    bne  t2, t3, fail

    li   gp, 9                  # sllw sign-extends bit 31 of the result
    li   t0, 1
    li   t1, 31
    sllw t2, t0, t1
    li   t3, -0x80000000
    bne  t2, t3, fail

    li   gp, 10                 # sllw uses only the low 5 bits of the amount
    li   t1, 32
    sllw t2, t0, t1
    bne  t2, t0, fail

    li   gp, 11                 # srlw works on the low 32 bits
    li   t0, -0x80000000
    li   t1, 31
    srlw t2, t0, t1
    li   t3, 1
    bne  t2, t3, fail

    li   gp, 12                 # sraw
    sraw t2, t0, t1
    li   t3, -1
    bne  t2, t3, fail

    li   gp, 13                 # slliw
    li   t0, 0x40000000
    slliw t2, t0, 1
    li   t3, -0x80000000
    bne  t2, t3, fail

    li   gp, 14                 # srliw
    li   t0, -1
    srliw t2, t0, 28
    li   t3, 0xf
    bne  t2, t3, fail

    li   gp, 15                 # sraiw
    sraiw t2, t0, 28
    bne  t2, t0, fail

    li   gp, 16                 # srliw ignores the upper 32 bits
    li   t0, 0x100000010
    srliw t2, t0, 4
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
