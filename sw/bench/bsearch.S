# bsearch.S - 250 lower-bound binary searches over a sorted 256-doubleword
# array. Shows: data-dependent, hard-to-predict branches (what bounds ANY
# predictor). array[i] = 4*i (0, 4, ..., 1020, built at start-up).
.equ HALT, 0x10000008
.equ N, 256
.equ NKEYS, 250
.equ SEED, 0x1234567

    la   s0, array
    li   s5, N
    mv   t1, s0                 # fill: array[i] = 4*i
    li   t0, 0
    li   t2, N
fill:
    sd   t0, 0(t1)
    addi t0, t0, 4
    addi t1, t1, 8
    addi t2, t2, -1
    bnez t2, fill

    li   s1, NKEYS
    li   s2, SEED
    li   s3, 0
    li   s4, 0
search:
    slli t0, s2, 13             # xorshift64 step
    xor  s2, s2, t0
    srli t0, s2, 7
    xor  s2, s2, t0
    slli t0, s2, 17
    xor  s2, s2, t0
    andi t3, s2, 1023           # key
    li   t0, 0                  # lo = 0
    mv   t1, s5                 # hi = N
probe:
    bgeu t0, t1, probe_done     # while lo < hi
    add  t2, t0, t1
    srli t2, t2, 1              # mid = (lo + hi) / 2
    slli t4, t2, 3
    add  t4, t4, s0
    ld   t5, 0(t4)              # a[mid]
    bge  t5, t3, go_left        # a[mid] >= key: hi = mid   (unpredictable)
    addi t0, t2, 1              # else lo = mid + 1
    j    probe
go_left:
    mv   t1, t2
    j    probe
probe_done:
    add  s3, s3, t0             # accumulate lower-bound index
    beq  t0, s5, next_key       # lo == N: key above every element, no hit
    slli t4, t0, 3
    add  t4, t4, s0
    ld   t5, 0(t4)
    bne  t5, t3, next_key       # a[lo] != key: not found
    addi s4, s4, 1              # exact hit
next_key:
    addi s1, s1, -1
    bnez s1, search

    li   gp, 1                  # sum of lower-bound indices
    li   t5, 31966
    bne  s3, t5, fail
    li   gp, 2                  # number of exact hits
    li   t5, 65
    bne  s4, t5, fail

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
array:
    .zero 8 * N
