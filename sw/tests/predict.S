# predict.S - control flow that a branch predictor gets wrong.
.equ HALT, 0x10000008

    la   t0, handler
    csrw mtvec, t0
    la   s0, data
    li   s5, 0

    li   gp, 1                  # a loop run twice: its branch is predicted taken
    li   a0, 0                  # by the time it exits, and exits twice
    li   s1, 2
outer1:
    li   t0, 10
inner1:
    add  a0, a0, t0
    addi t0, t0, -1
    bnez t0, inner1
    addi s1, s1, -1
    bnez s1, outer1
    li   t3, 110
    bne  a0, t3, fail

    li   gp, 2                  # a branch taken 20 times and then never again
    li   a0, 0
    li   a1, 0
    li   t0, 0
    li   t1, 20
    li   t2, 40
flip:
    blt  t0, t1, flip_low
    addi a1, a1, 1
    j    flip_next
flip_low:
    addi a0, a0, 1
flip_next:
    addi t0, t0, 1
    blt  t0, t2, flip
    li   t3, 20
    bne  a0, t3, fail
    bne  a1, t3, fail

    li   gp, 3                  # a branch that alternates every time
    li   a0, 0
    li   a1, 0
    li   t0, 0
    li   t2, 30
alt:
    andi t1, t0, 1
    beqz t1, alt_even
    addi a1, a1, 3
    j    alt_next
alt_even:
    addi a0, a0, 1
alt_next:
    addi t0, t0, 1
    blt  t0, t2, alt
    li   t3, 15
    bne  a0, t3, fail
    li   t3, 45
    bne  a1, t3, fail

    li   gp, 4                  # an indirect call whose target alternates, through
    li   a0, 0                  # a pointer loaded just before the jump
    li   s1, 0
    li   s2, 12
ind:
    andi t1, s1, 1
    slli t1, t1, 3
    add  t1, t1, s0
    ld   t2, 0(t1)
    jalr ra, 0(t2)
    addi s1, s1, 1
    blt  s1, s2, ind
    li   t3, 606
    bne  a0, t3, fail

    li   gp, 5                  # one function called from two places: its return
    li   a0, 0                  # goes to a different address every time
    li   s1, 0
    li   s2, 10
two:
    call f_one
    addi a0, a0, 10
    call f_one
    addi a0, a0, 1000
    addi s1, s1, 1
    blt  s1, s2, two
    li   t3, 10120
    bne  a0, t3, fail

    li   gp, 6                  # a store on the fall-through path of a branch that
    li   t0, 0                  # is taken six times and then not taken: the store
    li   t2, 8                  # must be squashed six times and then happen
    li   a0, 0
sh:
    slti t1, t0, 6
    bnez t1, sh_skip
    sd   t0, 16(s0)
    addi a0, a0, 1
sh_skip:
    addi t0, t0, 1
    blt  t0, t2, sh
    ld   t3, 16(s0)
    li   t4, 7
    bne  t3, t4, fail
    li   t4, 2
    bne  a0, t4, fail

    li   gp, 7                  # the opposite: a store behind a branch that is not
    li   t0, 0                  # taken six times and then taken
    li   a0, 0
sn:
    slti t1, t0, 6
    beqz t1, sn_out
    addi a0, a0, 1
    sd   a0, 24(s0)
    addi t0, t0, 1
    j    sn
sn_out:
    ld   t3, 24(s0)
    li   t4, 6
    bne  t3, t4, fail

    li   gp, 8                  # a branch decided by a value loaded just before it,
    ld   s3, 32(s0)             # following a bit pattern from memory
    li   t0, 0
    li   a0, 0
    li   t2, 16
pat:
    srl  t1, s3, t0
    andi t1, t1, 1
    sd   t1, 40(s0)
    ld   t4, 40(s0)
    beqz t4, pat_zero
    addi a0, a0, 1
pat_zero:
    addi t0, t0, 1
    blt  t0, t2, pat
    li   t3, 10                 # 0xb38f has ten bits set
    bne  a0, t3, fail

    li   gp, 9                  # a trap in every iteration of a loop
    li   t0, 6
    li   a0, 0
tl:
    ecall
    addi a0, a0, 1
    addi t0, t0, -1
    bnez t0, tl
    li   t3, 6
    bne  a0, t3, fail
    bne  s5, t3, fail

    li   gp, 10                 # two branches 256 bytes apart, which share one
    li   a0, 0                  # entry of a 64-entry predictor, run alternately
    li   s1, 8
    j    bx
    .align 8
bx: beq  x0, x0, bx_taken
    j    fail
bx_taken:
    addi a0, a0, 1
    j    by
    .align 8
by: beq  x0, x0, by_taken
    j    fail
by_taken:
    addi a0, a0, 10
    addi s1, s1, -1
    beqz s1, done10
    j    bx
done10:
    li   t3, 88
    bne  a0, t3, fail

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

f_one:
    addi a0, a0, 1
    ret
f_hundred:
    addi a0, a0, 100
    ret

handler:
    addi s5, s5, 1
    csrr t6, mepc
    addi t6, t6, 4
    csrw mepc, t6
    mret

    .align 3
data:
    .dword f_one
    .dword f_hundred
    .dword 0
    .dword 0
    .dword 0xb38f
    .dword 0
