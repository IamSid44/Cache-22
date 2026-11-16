# dep_chain.S - one serial dependency chain, every instruction needs the last.
# Shows: the LIMIT of out-of-order execution and superscalar issue.
.equ HALT, 0x10000008
.equ ITER, 1500

    li   a0, 1
    li   s0, ITER
loop:
    addi a0, a0, 1031           # f_0
    slli a0, a0, 1
    xori a0, a0, 0x5a5
    srli a0, a0, 1
    addi a0, a0, 517            # f_1
    slli a0, a0, 1
    xori a0, a0, 0x3c3
    srli a0, a0, 1
    addi a0, a0, 777            # f_2
    slli a0, a0, 1
    xori a0, a0, 0x6d9
    srli a0, a0, 1
    addi a0, a0, 1337           # f_3
    slli a0, a0, 1
    xori a0, a0, 0x2b7
    srli a0, a0, 1
    addi s0, s0, -1
    bnez s0, loop

    li   gp, 1                  # final chain value
    li   t5, 5501145
    bne  a0, t5, fail

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
