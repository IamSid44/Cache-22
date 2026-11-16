# fill.S - a 16 KiB array written from end to end, twice, and hardly read.
# Shows: what a store that misses should do (allocate or not), and merging
# in the write buffer.
.equ HALT, 0x10000008
.equ N, 2048
.equ ARRAY, 0x4000

    li   s0, ARRAY
    li   s1, ARRAY + 8 * N
    li   s2, 0
pass_start:
    mv   t0, s0
    mv   t1, s2                 # a[i] = 3i + pass
fill:
    sd   t1, 0(t0)
    addi t1, t1, 3
    addi t0, t0, 8
    bne  t0, s1, fill
    addi s2, s2, 1
    li   t2, 2
    bne  s2, t2, pass_start

    li   gp, 1                  # a[0]
    ld   t2, 0(s0)
    li   t5, 1
    bne  t2, t5, fail
    li   gp, 2                  # a[1]
    ld   t2, 8(s0)
    li   t5, 4
    bne  t2, t5, fail
    li   gp, 3                  # a[1000]
    li   t3, ARRAY + 8 * 1000
    ld   t2, 0(t3)
    li   t5, 3001
    bne  t2, t5, fail
    li   gp, 4                  # a[2047]
    ld   t2, -8(s1)
    li   t5, 6142
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
array:
    .zero 8
