# miss_indep.S - one cache-missing load per iteration plus independent ALU work.
# Shows: out-of-order execution under cache misses.
.equ HALT, 0x10000008
.equ N, 2048
.equ ITER, 1200
.equ SEED, 0x2545F491
.equ ARRAY, 0x4000

    li   s0, ARRAY
    li   s4, ARRAY + 8 * N
    mv   t0, s0                 # fill: array[i] = i + 1, unrolled by 4
    li   t1, 1
fill:
    sd   t1, 0(t0)
    addi t1, t1, 1
    sd   t1, 8(t0)
    addi t1, t1, 1
    sd   t1, 16(t0)
    addi t1, t1, 1
    sd   t1, 24(t0)
    addi t1, t1, 1
    addi t0, t0, 32
    bne  t0, s4, fill

    li   s1, 0
    li   s2, SEED
    li   s3, ITER
    li   a0, 0
    li   a1, 0
    li   a2, 0
    li   a3, 0
loop:
    slli t0, s2, 13             # xorshift64 step: next random address
    xor  s2, s2, t0
    srli t0, s2, 7
    xor  s2, s2, t0
    slli t0, s2, 17
    xor  s2, s2, t0
    andi t0, s2, 2047           # random doubleword index
    slli t0, t0, 3
    add  t0, t0, s0
    ld   t1, 0(t0)              # likely cache miss
    add  s1, s1, t1             # only user of the loaded value
    addi a0, a0, 3              # 8 ALU ops independent of the load
    addi a1, a1, 5
    xori a2, a2, 0x55
    addi a3, a3, 7
    add  a0, a0, a1
    xor  a2, a2, a3
    slli a4, a0, 1
    addi a5, a2, 1
    addi s3, s3, -1
    bnez s3, loop

    li   gp, 1                  # load sum
    li   t5, 1208675
    bne  s1, t5, fail
    li   gp, 2                  # independent chains
    li   t5, 3606600
    bne  a0, t5, fail
    li   gp, 3
    li   t5, 4672
    bne  a2, t5, fail
    li   gp, 4
    li   t5, 7213200
    bne  a4, t5, fail
    li   gp, 5
    li   t5, 4673
    bne  a5, t5, fail

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
array:
    .zero 8 * N
