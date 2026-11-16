# lru_fifo.S - three lines used all the time, among lines used once in a while.
# Shows: replacement policy (LRU against FIFO).
.equ HALT, 0x10000008
.equ ROUNDS, 1920
.equ HOT, 0x4000
.equ COLD, 0x4300
.equ NCOLD, 32
.equ STEP, 256

    li   s0, HOT
    li   s1, HOT + STEP
    li   s2, HOT + 2 * STEP
    li   s6, COLD
    li   s4, COLD + STEP * NCOLD
    li   t0, 3                  # hot values
    sd   t0, 0(s0)
    li   t0, 5
    sd   t0, 0(s1)
    li   t0, 11
    sd   t0, 0(s2)
    mv   t4, s6                 # cold[k] = 7k + 1
    li   t0, 1
init:
    sd   t0, 0(t4)
    addi t0, t0, 7
    addi t4, t4, STEP
    bne  t4, s4, init

    li   s3, 0
    li   s5, ROUNDS
    mv   t4, s6
round:
    ld   t0, 0(s0)              # the three hot lines
    ld   t1, 0(s1)
    ld   t2, 0(s2)
    ld   t3, 0(t4)              # one cold line
    add  s3, s3, t0
    add  s3, s3, t1
    add  s3, s3, t2
    add  s3, s3, t3
    addi t4, t4, STEP
    bne  t4, s4, no_wrap
    mv   t4, s6
no_wrap:
    addi s5, s5, -1
    bnez s5, round

    li   gp, 1                  # sum over all rounds
    li   t5, 246720
    bne  s3, t5, fail

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

    .org 0x4000
data:
    .zero 8
