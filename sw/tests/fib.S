# fib.S - iterative Fibonacci table, fib(0..40), plus a console message.
# Self-checking: exit code 0 = pass.
.equ HALT, 0x10000008
.equ CONSOLE, 0x10000000
.equ N, 41

    la   s0, table
    li   t0, 0                  # fib(i)
    li   t1, 1                  # fib(i+1)
    li   t2, 0                  # i
    li   t3, N
fill:
    slli t4, t2, 3
    add  t4, t4, s0
    sd   t0, 0(t4)
    add  t5, t0, t1
    mv   t0, t1
    mv   t1, t5
    addi t2, t2, 1
    blt  t2, t3, fill

    li   gp, 1
    ld   t4, 80(s0)             # fib(10)
    li   t5, 55
    bne  t4, t5, fail
    li   gp, 2
    ld   t4, 320(s0)            # fib(40)
    li   t5, 102334155
    bne  t4, t5, fail

    la   t0, message            # print "fib ok\n"
    li   t1, CONSOLE
print:
    lbu  t2, 0(t0)
    beqz t2, pass
    sb   t2, 0(t1)
    addi t0, t0, 1
    j    print

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

message:
    .byte 0x66, 0x69, 0x62, 0x20, 0x6f, 0x6b, 0x0a, 0
    .align 3
table:
    .zero 8 * N
