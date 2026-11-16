# sieve.S - sieve of Eratosthenes over 0..1999 with a byte array, then count.
# Shows: general integer code (byte loads/stores, nested loops, mixed
# branches). flags[n] = 0 means "not yet marked composite".
.equ HALT, 0x10000008
.equ N, 2000
.equ LIMIT, 45
.equ FLAGS, 0x4000

    li   s0, FLAGS
    li   s2, N
    li   s3, 1
    li   s6, LIMIT
    li   s1, 2
outer:
    add  t0, s0, s1
    lbu  t1, 0(t0)
    bnez t1, next_i             # i already composite: nothing to mark
    add  t2, s1, s1             # j = 2i
    bge  t2, s2, next_i
mark:
    add  t4, s0, t2
    sb   s3, 0(t4)              # flags[j] = 1
    add  t2, t2, s1             # j += i
    blt  t2, s2, mark
next_i:
    addi s1, s1, 1
    blt  s1, s6, outer

    li   s4, 0                  # count primes in 2..N-1
    li   s5, 0
    li   s1, 2
    addi t0, s0, 2              # pointer to flags[2]
count:
    lbu  t1, 0(t0)
    bnez t1, count_next         # composite
    addi s4, s4, 1
    add  s5, s5, s1
count_next:
    addi t0, t0, 1
    addi s1, s1, 1
    blt  s1, s2, count

    li   gp, 1                  # number of primes below 2000
    li   t5, 303
    bne  s4, t5, fail
    li   gp, 2                  # sum of the primes
    li   t5, 277050
    bne  s5, t5, fail

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
flags:
    .zero N
