# branch_alt.S - a loop whose inner conditional branch strictly alternates.
# Shows: the LIMIT of a 2-bit counter predictor (shown deliberately).
.equ HALT, 0x10000008
.equ N, 2500

    li   s0, N
    li   s1, 0
    li   s2, 0
    li   s3, 0
    li   s4, 0
loop:
    andi t0, s1, 1
    bnez t0, odd_path           # alternates not-taken / taken every iteration
even_path:
    add  s2, s2, s1             # even i: add into the even sum
    slli t1, s1, 2
    add  s4, s4, t1
    j    join
odd_path:
    sub  s3, s3, s1             # odd i: subtract from the odd accumulator
    addi t1, s1, 7
    add  s4, s4, t1
join:
    addi s1, s1, 1
    blt  s1, s0, loop

    li   gp, 1                  # even sum
    li   t5, 1561250
    bne  s2, t5, fail
    li   gp, 2                  # odd accumulator
    li   t5, -1562500
    bne  s3, t5, fail
    li   gp, 3                  # mixing sum
    li   t5, 7816250
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
