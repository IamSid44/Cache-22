# array_sum.S - 8 sequential passes summing a 2048-doubleword (16 KiB) array.
# Shows: cache spatial locality and next-line / stride prefetching. Layout:
# array at 0x4000..0x7fff (aligned to 32-byte lines).
.equ HALT, 0x10000008
.equ N, 2048
.equ PASSES, 8
.equ ARRAY, 0x4000

    li   s0, ARRAY
    li   s1, ARRAY + 8 * N
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
    bne  t0, s1, fill

    li   s2, 0
    li   s3, PASSES
pass_start:
    mv   t0, s0
sum_loop:
    ld   t3, 0(t0)              # one 32-byte line per iteration
    ld   t4, 8(t0)
    ld   t5, 16(t0)
    ld   t6, 24(t0)
    add  s2, s2, t3
    add  s2, s2, t4
    add  s2, s2, t5
    add  s2, s2, t6
    addi t0, t0, 32
    bne  t0, s1, sum_loop
    addi s3, s3, -1
    bnez s3, pass_start

    li   gp, 1                  # total over all passes
    li   t5, 16785408
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
array:
    .zero 8 * N
