# ilp_chains.S - four independent dependency chains advanced in lockstep.
# Shows: out-of-order execution and superscalar issue (high ILP).
.equ HALT, 0x10000008
.equ ITER, 1500

    li   a0, 1
    li   a1, 2
    li   a2, 3
    li   a3, 4
    li   s0, ITER
loop:
    addi a0, a0, 1031           # step 1: add
    addi a1, a1, 517
    addi a2, a2, 777
    addi a3, a3, 1337
    slli a0, a0, 1              # step 2: shift left
    slli a1, a1, 1
    slli a2, a2, 1
    slli a3, a3, 1
    xori a0, a0, 0x5a5          # step 3: xor
    xori a1, a1, 0x3c3
    xori a2, a2, 0x6d9
    xori a3, a3, 0x2b7
    srli a0, a0, 1              # step 4: shift right
    srli a1, a1, 1
    srli a2, a2, 1
    srli a3, a3, 1
    addi s0, s0, -1
    bnez s0, loop

    li   gp, 1                  # chain 0
    li   t5, 1547269
    bne  a0, t5, fail
    li   gp, 2                  # chain 1
    li   t5, 774322
    bne  a1, t5, fail
    li   gp, 3                  # chain 2
    li   t5, 1160927
    bne  a2, t5, fail
    li   gp, 4                  # chain 3
    li   t5, 1968516
    bne  a3, t5, fail

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
