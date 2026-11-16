# incl_victim.S - a counter that never leaves the first-level cache, and four
# buffers that share its set in the second-level cache. Shows: what
# inclusion costs (an "inclusion victim"), and what a victim buffer buys.
.equ HALT, 0x10000008
.equ ROUNDS, 500
.equ COUNTER, 0x4000
.equ BUFFERS, 0x4800
.equ SPAN, 2048

    li   s0, COUNTER
    li   s1, BUFFERS
    li   s2, SPAN
    sd   x0, 0(s0)              # counter = 0
    mv   t0, s1                 # buffers = 3, 5, 7, 11
    li   t3, 3
    sd   t3, 0(t0)
    add  t0, t0, s2
    li   t3, 5
    sd   t3, 0(t0)
    add  t0, t0, s2
    li   t3, 7
    sd   t3, 0(t0)
    add  t0, t0, s2
    li   t3, 11
    sd   t3, 0(t0)

    li   s3, ROUNDS
round:
    mv   t0, s1
    li   t1, 4
step:
    ld   t2, 0(s0)
    ld   t3, 0(t0)
    add  t2, t2, t3
    sd   t2, 0(s0)
    add  t0, t0, s2
    addi t1, t1, -1
    bnez t1, step
    addi s3, s3, -1
    bnez s3, round

    li   gp, 1                  # counter after all rounds
    ld   t2, 0(s0)
    li   t5, 13000
    bne  t2, t5, fail

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
