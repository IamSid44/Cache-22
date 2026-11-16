# ways_plus_one.S - five lines used in turn, in a set that holds four.
# Shows: the limit of LRU (set thrashing), and random replacement.
.equ HALT, 0x10000008
.equ PASSES, 800
.equ BASE, 0x4000
.equ NLINES, 5
.equ STEP, 256

    li   s0, BASE
    li   s1, BASE + STEP * NLINES
    mv   t0, s0                 # line k = k + 1
    li   t1, 1
init:
    sd   t1, 0(t0)
    addi t1, t1, 1
    addi t0, t0, STEP
    bne  t0, s1, init

    li   s2, 0
    li   s3, PASSES
pass_start:
    mv   t0, s0
line:
    ld   t1, 0(t0)
    add  s2, s2, t1
    addi t0, t0, STEP
    bne  t0, s1, line
    addi s3, s3, -1
    bnez s3, pass_start

    li   gp, 1                  # sum over all passes
    li   t5, 12000
    bne  s2, t5, fail

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
