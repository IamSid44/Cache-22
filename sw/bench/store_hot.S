# store_hot.S - a histogram: eight counters incremented over and over.
# Shows: what a store that hits should do (write-back or write-through), and
# the write buffer.
.equ HALT, 0x10000008
.equ N, 256
.equ PASSES, 12
.equ INPUT, 0x4000
.equ COUNTS, 0x5000

    li   s0, INPUT
    li   s1, INPUT + N
    li   s2, COUNTS
    mv   t0, s0                 # input[i] = ((i & (i >> 3)) ^ (i >> 6)) & 7
    li   t3, 0
gen:
    srli t1, t3, 3
    and  t1, t1, t3
    srli t2, t3, 6
    xor  t1, t1, t2
    andi t1, t1, 7
    sb   t1, 0(t0)
    addi t3, t3, 1
    addi t0, t0, 1
    bne  t0, s1, gen

    li   s3, PASSES
pass_start:
    mv   t0, s0
elem:
    lbu  t1, 0(t0)              # the value
    slli t1, t1, 3
    add  t1, t1, s2             # its counter
    ld   t2, 0(t1)
    addi t2, t2, 1
    sd   t2, 0(t1)
    addi t0, t0, 1
    bne  t0, s1, elem
    addi s3, s3, -1
    bnez s3, pass_start

    li   t3, 0                  # sum of the counters, and of value x count
    li   t4, 0
    li   t5, 0                  # value
    mv   t0, s2
total:
    ld   t2, 0(t0)
    add  t3, t3, t2
    mv   t1, t5                 # t4 += value * count, by repeated addition
weigh:
    beqz t1, weighed
    add  t4, t4, t2
    addi t1, t1, -1
    j    weigh
weighed:
    addi t5, t5, 1
    addi t0, t0, 8
    li   t1, 8
    bne  t5, t1, total

    li   gp, 1                  # sum of the counters
    li   t5, 3072
    bne  t3, t5, fail
    li   gp, 2                  # sum of value x count
    li   t5, 7680
    bne  t4, t5, fail
    li   gp, 3                  # counter 0
    ld   t2, 0(s2)
    li   t5, 576
    bne  t2, t5, fail
    li   gp, 4                  # counter 7
    ld   t2, 56(s2)
    li   t5, 192
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
input:
    .zero N
    .org 0x5000
counts:
    .zero 64
