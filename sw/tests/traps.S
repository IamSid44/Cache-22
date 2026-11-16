# traps.S - synchronous exceptions, mret, and the CSR instructions.
.equ HALT, 0x10000008
.equ OUTSIDE_RAM, 0x20000

    la   t0, handler
    csrw mtvec, t0
    li   s5, 0

    li   gp, 1                  # ecall: cause 11
at_ecall:
    ecall
    li   t3, 11
    bne  s2, t3, fail
    la   t3, at_ecall
    bne  s4, t3, fail
    li   t3, 1
    bne  s5, t3, fail

    li   gp, 2                  # ebreak: cause 3, mtval = its own address
at_ebreak:
    ebreak
    li   t3, 3
    bne  s2, t3, fail
    la   t3, at_ebreak
    bne  s3, t3, fail
    bne  s4, t3, fail

    li   gp, 3                  # illegal instruction: cause 2, mtval = encoding
    .word 0xffffffff
    li   t3, 2
    bne  s2, t3, fail
    li   t3, 0xffffffff
    bne  s3, t3, fail
    li   gp, 4
    .word 0
    li   t3, 2
    bne  s2, t3, fail
    bnez s3, fail

    # Reserved encodings inside otherwise legal opcodes are illegal too.
    li   gp, 20                 # OP with funct7 = 1
    li   s2, 99
    .word 0x02051533
    li   t3, 2
    bne  s2, t3, fail
    li   t3, 0x02051533
    bne  s3, t3, fail
    li   gp, 21                 # xor with the sub/sra bit set
    li   s2, 99
    .word 0x4000c533
    li   t3, 2
    bne  s2, t3, fail
    li   t3, 0x4000c533
    bne  s3, t3, fail
    li   gp, 22                 # slliw with a 6-bit shift amount
    li   s2, 99
    .word 0x0205151b
    li   t3, 2
    bne  s2, t3, fail
    li   t3, 0x0205151b
    bne  s3, t3, fail
    li   gp, 23                 # load with funct3 = 7
    li   s2, 99
    .word 0x00007003
    li   t3, 2
    bne  s2, t3, fail
    li   t3, 0x00007003
    bne  s3, t3, fail
    li   gp, 24                 # store with funct3 = 4
    li   s2, 99
    .word 0x00004023
    li   t3, 2
    bne  s2, t3, fail
    li   t3, 0x00004023
    bne  s3, t3, fail
    li   gp, 25                 # branch with funct3 = 2
    li   s2, 99
    .word 0x00002063
    li   t3, 2
    bne  s2, t3, fail
    li   t3, 0x00002063
    bne  s3, t3, fail
    li   gp, 26                 # jalr with funct3 = 1
    li   s2, 99
    .word 0x00001067
    li   t3, 2
    bne  s2, t3, fail
    li   t3, 0x00001067
    bne  s3, t3, fail

    li   gp, 5                  # misaligned load: cause 4, rd unchanged
    li   t0, 0x101
    li   t2, 77
    ld   t2, 0(t0)
    li   t3, 4
    bne  s2, t3, fail
    bne  s3, t0, fail
    li   t3, 77
    bne  t2, t3, fail

    li   gp, 6                  # misaligned store: cause 6, memory unchanged
    la   t0, scratch
    li   t1, -1
    sw   t1, 2(t0)
    li   t3, 6
    bne  s2, t3, fail
    addi t3, t0, 2
    bne  s3, t3, fail
    ld   t2, 0(t0)
    bnez t2, fail

    li   gp, 7                  # load outside RAM: cause 5
    li   t0, OUTSIDE_RAM
    ld   t2, 0(t0)
    li   t3, 5
    bne  s2, t3, fail
    bne  s3, t0, fail

    li   gp, 8                  # store outside RAM: cause 7
    sd   t2, 8(t0)
    li   t3, 7
    bne  s2, t3, fail
    addi t3, t0, 8
    bne  s3, t3, fail

    li   gp, 9                  # jump to a misaligned target: cause 0,
    la   t0, at_jalr            # reported on the jump, link register unchanged
    addi t0, t0, 6
    li   t5, 55
at_jalr:
    jalr t5, 0(t0)
    bnez s2, fail
    bne  s3, t0, fail
    la   t3, at_jalr
    bne  s4, t3, fail
    li   t3, 55
    bne  t5, t3, fail

    li   gp, 10                 # taken branch to a misaligned target traps
    li   s2, 99
at_branch:
    beq  x0, x0, at_branch + 6
    bnez s2, fail
    la   t3, at_branch
    bne  s4, t3, fail

    li   gp, 11                 # ...a not-taken one does not
    mv   t4, s5
    bne  x0, x0, at_branch + 6
    bne  t4, s5, fail
    li   t3, 17
    bne  s5, t3, fail

    li   gp, 12                 # csrrw / csrrs / csrrc
    li   t0, 0x1234
    csrw mscratch, t0
    csrr t2, mscratch
    bne  t2, t0, fail
    li   gp, 13
    li   t1, 0x00f
    csrrs t2, mscratch, t1
    bne  t2, t0, fail
    csrr t2, mscratch
    li   t3, 0x123f
    bne  t2, t3, fail
    li   gp, 14
    li   t1, 0x030
    csrrc t2, mscratch, t1
    bne  t2, t3, fail
    csrr t2, mscratch
    li   t3, 0x120f
    bne  t2, t3, fail

    li   gp, 15                 # immediate forms
    csrrwi t2, mscratch, 21
    bne  t2, t3, fail
    csrrsi t2, mscratch, 10
    li   t3, 21
    bne  t2, t3, fail
    csrrci t2, mscratch, 1
    li   t3, 31
    bne  t2, t3, fail
    csrr t2, mscratch
    li   t3, 30
    bne  t2, t3, fail

    li   gp, 16                 # mstatus: a trap saves MIE in MPIE and clears
    csrr t2, mstatus            # MIE; mret restores it. MPP is fixed at 3.
    li   t3, 0x1880
    bne  t2, t3, fail
    csrsi mstatus, 8
    ecall
    li   t3, 0x1880
    bne  s6, t3, fail
    csrr t2, mstatus
    li   t3, 0x1888
    bne  t2, t3, fail
    csrci mstatus, 8

    li   gp, 17                 # read-only and unimplemented CSRs
    li   s2, 99
    csrr t2, mhartid
    bnez t2, fail
    li   t3, 99
    bne  s2, t3, fail
    csrw mhartid, t0
    li   t3, 2
    bne  s2, t3, fail
    li   s2, 99
    csrr t2, 0x7c0
    bne  s2, t3, fail

    li   gp, 18                 # mepc keeps only 4-byte aligned addresses
    li   t0, 0x103
    csrw mepc, t0
    csrr t2, mepc
    li   t3, 0x100
    bne  t2, t3, fail

    li   gp, 19                 # total number of traps taken
    li   t3, 20
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

handler:
    csrr s2, mcause
    csrr s3, mtval
    csrr s4, mepc
    csrr s6, mstatus
    addi s5, s5, 1
    addi t6, s4, 4
    csrw mepc, t6
    mret

    .align 3
scratch:
    .dword 0
