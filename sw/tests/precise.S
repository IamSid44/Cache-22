# precise.S - exceptions are precise: what must NOT happen around a trap.
.equ HALT, 0x10000008
.equ OUTSIDE_RAM, 0x20000

    la   t0, handler
    csrw mtvec, t0
    la   s0, buf
    li   s5, 0
    li   s7, 0
    li   s8, 0
    li   s9, 0

    li   gp, 1                  # a store behind an illegal instruction (found in
    li   t1, 0x111              # decode) never reaches memory
    li   s7, 4
    .word 0xffffffff
    sd   t1, 0(s0)              # skipped by the handler
    ld   t2, 0(s0)
    bnez t2, fail
    li   t3, 2
    bne  s2, t3, fail

    li   gp, 2                  # a register write behind a misaligned jump
    li   t2, 7                  # (found in execute) never happens
    la   t0, target2
    addi t0, t0, 2
    li   s7, 4
    jalr t4, 0(t0)
    li   t2, 99                 # skipped
    bnez s2, fail
    li   t3, 7
    bne  t2, t3, fail
target2:

    li   gp, 3                  # a CSR write behind a misaligned load (found in
    li   t0, 0x55               # the memory stage) never happens
    csrw mscratch, t0
    li   t1, 0x66
    addi t4, s0, 1
    li   s7, 4
    ld   t5, 0(t4)
    csrw mscratch, t1           # skipped
    csrr t2, mscratch
    bne  t2, t0, fail
    li   t3, 4
    bne  s2, t3, fail

    li   gp, 4                  # the store before a trap takes effect, the one
    li   t1, 0x222              # after it does not
    sd   t1, 8(s0)
    li   s7, 4
    ecall
    sd   t1, 16(s0)             # skipped
    ld   t2, 8(s0)
    bne  t2, t1, fail
    ld   t2, 16(s0)
    bnez t2, fail

    li   gp, 5                  # an older instruction that faults late (in the
    li   t0, OUTSIDE_RAM        # memory stage) is followed by a younger one that
    mv   t4, s5                 # faults early (in decode): the older one traps
    ld   t5, 0(t0)              # first, then the younger one
    .word 0xffffffff
    li   t3, 5
    bne  s3, t3, fail
    li   t3, 2
    bne  s2, t3, fail
    addi t4, t4, 2
    bne  t4, s5, fail

    li   gp, 6                  # the same pair with the younger one skipped: its
    mv   t4, s5                 # exception must vanish with it
    li   s7, 4
    ld   t5, 0(t0)
    .word 0xffffffff            # skipped
    addi t4, t4, 1
    bne  t4, s5, fail
    li   t3, 5
    bne  s2, t3, fail

    li   gp, 7                  # faulting instructions behind a taken branch or
    mv   t4, s5                 # jump are squashed and do not trap
    beq  x0, x0, over7
    .word 0xffffffff
    ecall
over7:
    j    over7b
    ld   t5, 1(s0)
over7b:
    bne  t4, s5, fail

    li   gp, 8                  # a trap followed at once by a taken jump: the
    mv   t4, s5                 # trap wins, the handler runs, then the jump
    ecall
    j    after8
    j    fail
after8:
    addi t4, t4, 1
    bne  t4, s5, fail
    li   t3, 11
    bne  s2, t3, fail

    li   gp, 9                  # a faulting address that was loaded by the
    addi t0, s0, 3              # instruction just before
    sd   t0, 24(s0)
    ld   t1, 24(s0)
    ld   t5, 0(t1)
    li   t3, 4
    bne  s2, t3, fail
    csrr t2, mtval
    bne  t2, t0, fail

    li   gp, 10                 # a CSR value used by the next instruction
    li   t0, 41
    csrw mscratch, t0
    csrr t1, mscratch
    addi t2, t1, 1
    li   t3, 42
    bne  t2, t3, fail

    li   gp, 11                 # CSR write then read, and a swap with rd == rs1
    csrw mscratch, t2
    csrr t1, mscratch
    bne  t1, t2, fail
    li   t0, 7
    csrrw t0, mscratch, t0
    li   t3, 42
    bne  t0, t3, fail
    csrr t1, mscratch
    li   t3, 7
    bne  t1, t3, fail

    li   gp, 12                 # a trap right after mtvec changes uses the new
    la   t0, handler2           # handler
    csrw mtvec, t0
    ecall
    la   t0, handler
    csrw mtvec, t0
    li   t3, 1
    bne  s9, t3, fail

    li   gp, 13                 # minstret counts retired instructions
    csrr t0, minstret
    nop
    nop
    csrr t1, minstret
    sub  t2, t1, t0
    li   t3, 3
    bne  t2, t3, fail

    li   gp, 14                 # a trap is not a retired instruction: between the
    csrr t0, minstret           # two reads retire the first csrr and the nine
    ecall                       # instructions of the handler
    csrr t1, minstret
    sub  t2, t1, t0
    li   t3, 10
    bne  t2, t3, fail

    li   gp, 15                 # writing minstret: the write itself then retires
    li   t0, 1000
    csrw minstret, t0
    csrr t1, minstret
    li   t3, 1001
    bne  t1, t3, fail

    li   gp, 16                 # a jump to an address outside RAM: the jump itself
    la   t0, handler3           # retires and writes its link register; the fetch
    csrw mtvec, t0              # at the target is what faults (cause 1), so mepc
    la   s10, back16            # and mtval are both the target
    li   t0, OUTSIDE_RAM
    li   t4, 0
    jalr t4, 0(t0)
after16:
    j    fail                   # never reached: handler3 returns to back16
back16:
    la   t0, handler
    csrw mtvec, t0
    li   t3, 1
    bne  s2, t3, fail
    li   t3, OUTSIDE_RAM
    bne  s3, t3, fail
    bne  s4, t3, fail
    la   t3, after16
    bne  t4, t3, fail

    li   gp, 17                 # nothing behind an mret was ever executed
    bnez s8, fail

    li   gp, 18                 # total traps taken through the main handler
    li   t3, 10
    bne  s5, t3, fail

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

handler:                        # nine instructions (check 14 relies on that)
    mv   s3, s2
    csrr s2, mcause
    csrr s4, mepc
    addi s5, s5, 1
    addi t6, s4, 4
    add  t6, t6, s7
    li   s7, 0
    csrw mepc, t6
    mret
    li   s8, 0xbad              # must never execute

handler2:
    addi s9, s9, 1
    csrr t6, mepc
    addi t6, t6, 4
    csrw mepc, t6
    mret
    li   s8, 0xbad              # must never execute

handler3:                       # records mcause, mtval, mepc; resumes at s10
    csrr s2, mcause
    csrr s3, mtval
    csrr s4, mepc
    csrw mepc, s10
    mret
    li   s8, 0xbad              # must never execute

    .align 3
buf:
    .dword 0, 0, 0, 0
