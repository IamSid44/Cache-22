# sort.S - bubble sort of 8 doublewords, then verify the order and checksum. A
# small kernel with data-dependent branches and load/store traffic.
# Self-checking: exit code 0 = pass.
.equ HALT, 0x10000008
.equ N, 8

    la   s0, array
    li   s1, N
outer:
    addi s1, s1, -1
    beqz s1, sorted
    li   t0, 0                  # i
    mv   t1, s0                 # &array[i]
inner:
    ld   t2, 0(t1)
    ld   t3, 8(t1)
    bge  t3, t2, no_swap        # signed compare
    sd   t3, 0(t1)
    sd   t2, 8(t1)
no_swap:
    addi t1, t1, 8
    addi t0, t0, 1
    blt  t0, s1, inner
    j    outer

sorted:
    li   gp, 1                  # ascending order
    mv   t1, s0
    li   t0, N - 1
    li   t4, 0                  # checksum
check:
    ld   t2, 0(t1)
    ld   t3, 8(t1)
    blt  t3, t2, fail
    add  t4, t4, t2
    addi t1, t1, 8
    addi t0, t0, -1
    bnez t0, check
    add  t4, t4, t3

    li   gp, 2                  # sum is preserved: 42-7+0+1000+13-300+5+99
    li   t5, 852
    bne  t4, t5, fail
    li   gp, 3                  # smallest and largest ended up at the ends
    ld   t2, 0(s0)
    li   t5, -300
    bne  t2, t5, fail
    ld   t2, 56(s0)
    li   t5, 1000
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

    .align 3
array:
    .dword 42, -7, 0, 1000, 13, -300, 5, 99
