# matadd.S - C += A + B for 16x16 doubleword matrices, 8 passes, then checksum.
# Shows: general array code (nested loops, shift-computed addresses,
# streaming).
.equ HALT, 0x10000008
.equ DIM, 16
.equ PASSES, 8
.equ MAT_A, 0x4000
.equ MAT_B, 0x4960
.equ MAT_C, 0x52c0

    li   s0, MAT_A
    li   s1, MAT_B
    li   s2, MAT_C
    li   s3, 0                  # init: r loop
init_row:
    li   s4, 0                  # c loop
init_col:
    slli t0, s3, 4
    add  t0, t0, s4             # t0 = 16r + c
    slli t1, t0, 3              # byte offset of element (r, c)
    add  t2, t1, s0
    sd   t0, 0(t2)              # A[r][c] = 16r + c
    slli t3, s4, 4
    add  t3, t3, s3             # 16c + r
    add  t2, t1, s1
    sd   t3, 0(t2)              # B[r][c] = 16c + r
    addi s4, s4, 1
    li   t4, DIM
    blt  s4, t4, init_col
    addi s3, s3, 1
    blt  s3, t4, init_row

    li   s5, PASSES
add_pass:
    li   s3, 0                  # r
add_row:
    li   s4, 0                  # c
add_col:
    slli t0, s3, 4
    add  t0, t0, s4
    slli t0, t0, 3              # byte offset of element (r, c)
    add  t1, t0, s0
    add  t2, t0, s1
    add  t3, t0, s2
    ld   a0, 0(t1)              # A[r][c]
    ld   a1, 0(t2)              # B[r][c]
    ld   a2, 0(t3)              # C[r][c]
    add  a0, a0, a1
    add  a2, a2, a0
    sd   a2, 0(t3)              # C += A + B
    addi s4, s4, 1
    li   t4, DIM
    blt  s4, t4, add_col
    addi s3, s3, 1
    blt  s3, t4, add_row
    addi s5, s5, -1
    bnez s5, add_pass

    li   gp, 1                  # checksum of C
    mv   t0, s2
    li   t1, DIM * DIM
    li   t2, 0
sum_c:
    ld   t3, 0(t0)
    add  t2, t2, t3
    addi t0, t0, 8
    addi t1, t1, -1
    bnez t1, sum_c
    li   t5, 522240
    bne  t2, t5, fail
    li   gp, 2                  # C[1][2]
    ld   t3, 8 * (16 + 2)(s2)
    li   t5, 408
    bne  t3, t5, fail
    li   gp, 3                  # C[15][14]
    ld   t3, 8 * (15 * 16 + 14)(s2)
    li   t5, 3944
    bne  t3, t5, fail

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
mat_a:
    .zero 8 * DIM * DIM
    .org 0x4960
mat_b:
    .zero 8 * DIM * DIM
    .org 0x52c0
mat_c:
    .zero 8 * DIM * DIM
