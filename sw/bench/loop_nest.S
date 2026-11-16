# loop_nest.S - three nested counted loops (12 x 12 x 32 = 4608 body runs).
# Shows: branch prediction (BTB + 2-bit counters).
.equ HALT, 0x10000008
.equ OUTER, 12
.equ MIDDLE, 12
.equ INNER, 32

    li   s0, OUTER
    li   s2, 0
    li   s3, 0
outer_loop:
    li   s1, MIDDLE
middle_loop:
    li   t2, INNER
inner_loop:
    add  s3, s3, t2             # body: accumulate the counter
    addi s2, s2, 1              # body: count the run
    addi t2, t2, -1
    bnez t2, inner_loop         # taken 31 of 32 times
    addi s1, s1, -1
    bnez s1, middle_loop        # taken 11 of 12 times
    addi s0, s0, -1
    bnez s0, outer_loop         # taken 11 of 12 times

    li   gp, 1                  # number of body runs
    li   t5, OUTER * MIDDLE * INNER
    bne  s2, t5, fail
    li   gp, 2                  # sum of the inner counter values
    li   t5, 76032
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
