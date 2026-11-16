# mem.S - loads and stores of every width, sign/zero extension, byte order.
# Self-checking: exit code 0 = pass, otherwise the number of the failed check.
.equ HALT, 0x10000008

    la   s0, pattern
    la   s1, buf

    li   gp, 1                  # ld
    ld   t2, 0(s0)
    li   t3, 0x8877665544332211
    bne  t2, t3, fail

    li   gp, 2                  # lb: little-endian, byte 0 is the low byte
    lb   t2, 0(s0)
    li   t3, 0x11
    bne  t2, t3, fail
    li   gp, 3                  # lb sign-extends
    lb   t2, 7(s0)
    li   t3, -0x78
    bne  t2, t3, fail
    li   gp, 4                  # lbu zero-extends
    lbu  t2, 7(s0)
    li   t3, 0x88
    bne  t2, t3, fail

    li   gp, 5                  # lh / lhu
    lh   t2, 0(s0)
    li   t3, 0x2211
    bne  t2, t3, fail
    li   gp, 6
    lh   t2, 6(s0)
    li   t3, -0x7789
    bne  t2, t3, fail
    li   gp, 7
    lhu  t2, 6(s0)
    li   t3, 0x8877
    bne  t2, t3, fail

    li   gp, 8                  # lw / lwu
    lw   t2, 0(s0)
    li   t3, 0x44332211
    bne  t2, t3, fail
    li   gp, 9
    lw   t2, 4(s0)
    li   t3, -0x778899ab
    bne  t2, t3, fail
    li   gp, 10
    lwu  t2, 4(s0)
    li   t3, 0x88776655
    bne  t2, t3, fail

    li   gp, 11                 # sb / sh / sw assemble a doubleword
    li   t0, 0xab
    sb   t0, 1(s1)
    li   t0, 0xcdef
    sh   t0, 2(s1)
    li   t0, 0x12345678
    sw   t0, 4(s1)
    ld   t2, 0(s1)
    li   t3, 0x12345678cdefab00
    bne  t2, t3, fail

    li   gp, 12                 # narrow stores write only their own bytes
    sb   x0, 11(s1)
    ld   t2, 8(s1)
    li   t3, 0xffffffff00ffffff
    bne  t2, t3, fail
    li   gp, 13                 # and only the low bits of the source
    li   t0, 0x1ff
    sb   t0, 16(s1)
    ld   t2, 16(s1)
    li   t3, 0xff
    bne  t2, t3, fail

    li   gp, 14                 # sd / ld with a negative offset
    addi s2, s1, 32
    li   t0, 0x0123456789abcdef
    sd   t0, -8(s2)
    ld   t2, 24(s1)
    bne  t2, t0, fail

    li   gp, 15                 # store then load the same address back to back
    li   t0, 77
    sd   t0, 32(s1)
    ld   t2, 32(s1)
    addi t2, t2, 1
    li   t3, 78
    bne  t2, t3, fail

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
pattern:
    .dword 0x8877665544332211
buf:
    .dword 0
    .dword -1
    .dword 0
    .dword 0
    .dword 0
