# ws_walk.S - one load per cache line over a working set, round and round.
# Shows: the levels of a cache hierarchy (the "staircase"). Layout: WS_BYTES
# bytes at 0x4000, one doubleword used per 32-byte line.
.equ HALT, 0x10000008
.equ WS_BYTES, 4096
.equ READS, 4096
.equ BASE, 0x4000
.equ LINE, 32

    li   s0, BASE
    li   s1, BASE + WS_BYTES
    mv   t0, s0                 # line k = k + 1
    li   t1, 1
init:
    sd   t1, 0(t0)
    addi t1, t1, 1
    addi t0, t0, LINE
    bne  t0, s1, init

    li   s2, READS
    li   s3, 0
    li   s4, 0
    mv   t0, s0
    li   t1, 1
walk:
    ld   t2, 0(t0)
    add  s3, s3, t2
    add  s4, s4, t1
    addi t0, t0, LINE
    addi t1, t1, 1
    bne  t0, s1, same_pass
    mv   t0, s0                 # past the last line: start again
    li   t1, 1
same_pass:
    addi s2, s2, -1
    bnez s2, walk

    li   gp, 1                  # every load returned what its line was given
    bne  s3, s4, fail
    li   gp, 2                  # and the loads really were made
    beqz s3, fail

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
data:
    .zero 8
