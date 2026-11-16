# stride_walk.S - 14 passes reading one doubleword every 64 bytes of 32 KiB.
# Shows: the PC-indexed stride prefetcher (and the limit of next-line).
.equ HALT, 0x10000008
.equ COUNT, 512
.equ STRIDE, 64
.equ PASSES, 14
.equ REGION, 0x4000

    li   s0, REGION
    li   s1, REGION + STRIDE * COUNT
    mv   t0, s0                 # init: element k = k + 1 (only the visited words)
    li   t1, 1
init:
    sd   t1, 0(t0)
    addi t1, t1, 1
    addi t0, t0, STRIDE
    bne  t0, s1, init

    li   s2, 0
    li   s3, PASSES
pass_start:
    mv   t0, s0
walk:
    ld   t1, 0(t0)              # the only load: constant +64 byte delta
    add  s2, s2, t1
    addi t0, t0, STRIDE
    bne  t0, s1, walk
    addi s3, s3, -1
    bnez s3, pass_start

    li   gp, 1                  # total over all passes
    li   t5, 1838592
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
region:
    .zero 32768
