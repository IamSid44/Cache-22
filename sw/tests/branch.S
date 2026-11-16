# branch.S - conditional branches, jal/jalr, loops, call/return with a stack.
# Self-checking: exit code 0 = pass, otherwise the number of the failed check.
.equ HALT, 0x10000008
.equ STACK_TOP, 0x8000

    li   sp, STACK_TOP
    li   t0, -1
    li   t1, 1

    li   gp, 1                  # beq
    beq  t0, t0, b1
    j    fail
b1: beq  t0, t1, fail

    li   gp, 2                  # bne
    bne  t0, t1, b2
    j    fail
b2: bne  t0, t0, fail

    li   gp, 3                  # blt is signed: -1 < 1
    blt  t0, t1, b3
    j    fail
b3: blt  t1, t0, fail
    blt  t0, t0, fail

    li   gp, 4                  # bltu is unsigned: 1 < 0xffff...f
    bltu t1, t0, b4
    j    fail
b4: bltu t0, t1, fail

    li   gp, 5                  # bge, including equality
    bge  t1, t0, b5
    j    fail
b5: bge  t0, t0, b5b
    j    fail
b5b: bge t0, t1, fail

    li   gp, 6                  # bgeu
    bgeu t0, t1, b6
    j    fail
b6: bgeu t1, t0, fail

    li   gp, 7                  # backward branch: sum 1..10
    li   t2, 0
    li   t3, 10
loop:
    add  t2, t2, t3
    addi t3, t3, -1
    bnez t3, loop
    li   t4, 55
    bne  t2, t4, fail

    li   gp, 8                  # jal writes the return address
    jal  ra, f_link
after_jal:
    li   gp, 9
    li   t4, 99
    bne  a0, t4, fail

    li   gp, 10                 # jalr with an offset, link in t5
    la   t2, tgt_a
    addi t2, t2, -4
    jalr t5, 4(t2)
after_jalr:
    j    fail
tgt_a:
    la   t4, after_jalr
    bne  t5, t4, fail

    li   gp, 11                 # jalr clears bit 0 of the target
    la   t2, tgt_b
    addi t2, t2, 1
    jalr t5, 0(t2)
    j    fail
tgt_b:

    li   gp, 12                 # jalr with rd == rs1 uses the old value
    la   t2, tgt_c
    jalr t2, 0(t2)
after_c:
    j    fail
tgt_c:
    la   t4, after_c
    bne  t2, t4, fail

    li   gp, 13                 # nested calls: sum_to(6) = 21, recursive
    li   a0, 6
    call sum_to
    li   t4, 21
    bne  a0, t4, fail

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

f_link:
    la   t4, after_jal
    bne  ra, t4, fail
    li   a0, 99
    ret

# a0 = a0 + (a0-1) + ... + 1
sum_to:
    beqz a0, sum_done
    addi sp, sp, -16
    sd   ra, 8(sp)
    sd   a0, 0(sp)
    addi a0, a0, -1
    call sum_to
    ld   t0, 0(sp)
    ld   ra, 8(sp)
    addi sp, sp, 16
    add  a0, a0, t0
sum_done:
    ret
