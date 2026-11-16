# ooo.S - cases that only matter when instructions execute out of order.
.equ HALT, 0x10000008

    la   t0, handler
    csrw mtvec, t0
    la   s0, buf
    li   s5, 0

    li   gp, 1                  # a store with a late address, then a load with an
    li   t0, 0                  # early address to the same place
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    slli t0, t0, 3              # 48
    add  t1, s0, t0
    li   t2, 0xaa
    sd   t2, 0(t1)
    ld   t3, 48(s0)
    bne  t3, t2, fail

    li   gp, 2                  # a store with late data
    li   t0, 7
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    sd   t0, 0(s0)
    ld   t3, 0(s0)
    li   t4, 13
    bne  t3, t4, fail

    li   gp, 3                  # a byte store with a late address into a doubleword
    li   t2, -1                 # that is then loaded whole
    sd   t2, 8(s0)
    li   t0, 0
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 6              # 11
    add  t1, s0, t0
    sb   x0, 0(t1)              # byte 3 of the doubleword at buf + 8
    ld   t3, 8(s0)
    li   t4, 0xffffffff00ffffff
    bne  t3, t4, fail

    li   gp, 4                  # a doubleword store with a late address, then a
    li   t2, 0x1122334455667788 # byte load from inside it
    li   t0, 0
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    slli t0, t0, 2              # 16
    add  t1, s0, t0
    sd   t2, 0(t1)
    lbu  t3, 21(s0)             # byte 5
    li   t4, 0x33
    bne  t3, t4, fail

    li   gp, 5                  # two stores to one place both in flight, then a
    li   t2, 1                  # load: it must see the younger store
    li   t3, 2
    sd   t2, 24(s0)
    sd   t3, 24(s0)
    ld   t4, 24(s0)
    bne  t4, t3, fail

    li   gp, 6                  # a store with a late address that turns out not to
    li   t5, 0x55               # overlap: the load is independent of it
    sd   t5, 32(s0)
    li   t0, 0
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    slli t0, t0, 3              # 40
    add  t1, s0, t0
    li   t2, 0x66
    sd   t2, 0(t1)
    ld   t3, 32(s0)
    bne  t3, t5, fail
    ld   t3, 40(s0)
    bne  t3, t2, fail

    li   gp, 7                  # a reader of a0 is still waiting for its other
    li   a0, 1                  # operand when a0 is written again: it keeps 1
    li   t0, 0
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    add  a1, a0, t0             # 1 + 6
    li   a0, 100
    add  a2, a0, x0
    li   t4, 7
    bne  a1, t4, fail
    li   t4, 100
    bne  a2, t4, fail

    li   gp, 8                  # one register written again and again, each value
    li   a0, 0                  # read once
    addi a0, a0, 1
    mv   a3, a0
    addi a0, a0, 2
    mv   a4, a0
    addi a0, a0, 3
    mv   a5, a0
    addi a0, a0, 4
    li   t4, 10
    bne  a0, t4, fail
    li   t4, 1
    bne  a3, t4, fail
    li   t4, 3
    bne  a4, t4, fail
    li   t4, 6
    bne  a5, t4, fail

    li   gp, 9                  # a value carried through memory round a loop: each
    sd   x0, 48(s0)             # load depends on the store of the iteration before
    li   t0, 20
carry:
    ld   t1, 48(s0)
    addi t1, t1, 3
    sd   t1, 48(s0)
    addi t0, t0, -1
    bnez t0, carry
    ld   t3, 48(s0)
    li   t4, 60
    bne  t3, t4, fail

    li   gp, 10                 # a branch decided late; the wrong path writes a
    li   a0, 5                  # register and memory, and none of it may remain
    sd   x0, 56(s0)
    li   t0, 0
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    li   t1, 6
    beq  t0, t1, taken10
    li   a0, 99                 # wrong path
    sd   a0, 56(s0)             # wrong path
taken10:
    li   t4, 5
    bne  a0, t4, fail
    ld   t3, 56(s0)
    bnez t3, fail

    li   gp, 11                 # wrong-path instructions that would fault: a load
    mv   t5, s5                 # from an unmapped address and an illegal word
    li   t0, 0
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    li   t1, 6
    li   t2, 0x20000
    beq  t0, t1, taken11
    ld   t3, 0(t2)              # wrong path
    .word 0xffffffff            # wrong path
taken11:
    bne  t5, s5, fail

    li   gp, 12                 # an older load whose faulting address is known late,
    li   t0, 0                  # followed by an illegal word known at once: the
    addi t0, t0, 1              # load traps first
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    slli t0, t0, 15             # 0x30000, outside RAM
    ld   t3, 0(t0)
    .word 0xffffffff
    li   t4, 5
    bne  s3, t4, fail           # the trap before last: load access fault
    li   t4, 2
    bne  s2, t4, fail           # the last trap: illegal instruction

    li   gp, 13                 # two loads, the older one with a late address
    li   t2, 0x77
    sd   t2, 64(s0)
    li   t2, 0x88
    sd   t2, 72(s0)
    li   t0, 0
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 1
    addi t0, t0, 58             # 64
    add  t1, s0, t0
    ld   a1, 0(t1)
    ld   a2, 72(s0)
    li   t4, 0x77
    bne  a1, t4, fail
    li   t4, 0x88
    bne  a2, t4, fail

    li   gp, 14                 # x0 as a destination is never renamed
    addi x0, x0, 5
    add  a0, x0, x0
    bnez a0, fail

    li   gp, 15                 # an indirect jump through a register that is late
    la   t1, target15
    addi t1, t1, -6
    addi t1, t1, 1
    addi t1, t1, 1
    addi t1, t1, 1
    addi t1, t1, 1
    addi t1, t1, 1
    addi t1, t1, 1
    jalr a1, 0(t1)
return15:
    j    fail
target15:
    la   t4, return15
    bne  a1, t4, fail

    li   gp, 17                 # a store whose address comes from a load that
    la   t0, far                # misses the cache, then a load with an early
    li   t2, 0xbb               # address to the same place: it must not pass the
    ld   t1, 0(t0)              # store (far[0] holds the address buf + 48)
    sd   t2, 0(t1)
    ld   t3, 48(s0)
    bne  t3, t2, fail

    li   gp, 18                 # the same with the store's data coming from the
    ld   t2, 8(t0)              # missing load (far[1] holds 0x1234)
    sd   t2, 0(s0)
    ld   t3, 0(s0)
    li   t4, 0x1234
    bne  t3, t4, fail

    li   gp, 19                 # and with two loads behind the store, one to the
    la   t0, far2               # stored location and one elsewhere
    li   t2, 0xcc
    ld   t1, 0(t0)              # far2[0] holds the address buf + 56
    sd   t2, 0(t1)
    ld   t3, 64(s0)             # elsewhere: 0x77 from check 13
    ld   t4, 56(s0)
    bne  t4, t2, fail
    li   t5, 0x77
    bne  t3, t5, fail

    li   gp, 16                 # exactly the two traps of check 12 were taken
    li   t4, 2
    bne  s5, t4, fail

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

handler:                        # s2 = cause, s3 = the cause before, s5 = count
    mv   s3, s2
    csrr s2, mcause
    addi s5, s5, 1
    csrr t6, mepc
    addi t6, t6, 4
    csrw mepc, t6
    mret

    .align 3
buf:
    .dword 0, 0, 0, 0, 0, 0, 0, 0, 0, 0

# Pointers kept well away from everything else, each in a cache line that is
# touched for the first time by the load that needs it.
    .zero 256
far:
    .dword buf + 48
    .dword 0x1234
    .zero 256
far2:
    .dword buf + 56
