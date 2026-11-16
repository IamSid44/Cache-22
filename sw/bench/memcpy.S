# memcpy.S - copy a 4 KiB buffer doubleword by doubleword, 8 times, then verify.
# Shows: general streaming code (cache, prefetch and loop prediction
# trends).
.equ HALT, 0x10000008
.equ N, 512
.equ PASSES, 8
.equ SRC, 0x4000
.equ DST, 0x5160

    li   s0, SRC
    li   s1, DST
    li   s2, SRC + 8 * N
    mv   t0, s0                 # fill: src[i] = 725 * (i + 1)
    li   t4, 0x2d5
    li   t5, 0x2d5
fill:
    sd   t4, 0(t0)
    add  t4, t4, t5
    addi t0, t0, 8
    bne  t0, s2, fill

    li   s3, PASSES
copy_pass:
    mv   t0, s0
    mv   t1, s1
copy:
    ld   t2, 0(t0)
    sd   t2, 0(t1)
    addi t0, t0, 8
    addi t1, t1, 8
    bne  t0, s2, copy
    addi s3, s3, -1
    bnez s3, copy_pass

    li   gp, 1                  # destination equals source, word by word
    mv   t0, s0
    mv   t1, s1
    li   s4, 0
verify:
    ld   t2, 0(t0)
    ld   t3, 0(t1)
    bne  t2, t3, fail
    add  s4, s4, t3
    addi t0, t0, 8
    addi t1, t1, 8
    bne  t0, s2, verify
    li   gp, 2                  # checksum of the copy
    li   t5, 95212800
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

    .org 0x4000
src:
    .zero 8 * N
    .org 0x5160
dst:
    .zero 8 * N
