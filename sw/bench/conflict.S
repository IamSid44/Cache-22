# conflict.S - two 64-doubleword arrays exactly 4096 bytes apart, read
# alternately. Shows: cache associativity (conflict misses). Layout: array A
# at 0x4000..0x41ff, array B at 0x5000..0x51ff (B = A + 4096).
.equ HALT, 0x10000008
.equ N, 64
.equ PASSES, 60
.equ ARRAY_A, 0x4000
.equ ARRAY_B, 0x5000            # ARRAY_A + 4096

    li   s0, ARRAY_A
    li   s1, ARRAY_B
    li   s4, ARRAY_A + 8 * N
    mv   t2, s0                 # fill A[i] = i + 1 and B[i] = 3i + 7
    mv   t3, s1
    li   t0, 1
    li   t1, 7
fill:
    sd   t0, 0(t2)
    sd   t1, 0(t3)
    addi t0, t0, 1
    addi t1, t1, 3
    addi t2, t2, 8
    addi t3, t3, 8
    bne  t2, s4, fill

    li   s2, 0
    li   s3, 0
    li   s5, PASSES
pass_start:
    mv   t2, s0
    mv   t3, s1
elem:
    ld   t0, 0(t2)              # A[i]
    ld   t1, 0(t3)              # B[i]: same cache index as A[i]
    add  s2, s2, t0
    add  s3, s3, t1
    addi t2, t2, 8
    addi t3, t3, 8
    bne  t2, s4, elem
    addi s5, s5, -1
    bnez s5, pass_start

    li   gp, 1                  # sum of A over all passes
    li   t5, 124800
    bne  s2, t5, fail
    li   gp, 2                  # sum of B over all passes
    li   t5, 389760
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
arr_a:
    .zero 8 * N
    .org 0x5000
arr_b:
    .zero 8 * N
