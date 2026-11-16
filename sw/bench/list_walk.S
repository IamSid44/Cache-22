# list_walk.S - pointer chasing through a scrambled 256-node linked list.
# Shows: the LIMIT of prefetching (and of any cache without more capacity).
.equ HALT, 0x10000008
.equ NODES, 256
.equ STEP, 37
.equ PASSES, 12
.equ NODE_BASE, 0x4000

    li   s0, NODE_BASE
    li   s6, 0                  # x = 37*k mod 256, advanced by repeated addition
    mv   t3, s0                 # previous node (slot 0 is list node 0)
    li   t4, 1                  # payload = k + 1
    li   t5, NODES + 1
build:
    srli t1, s6, 3
    xor  t1, t1, s6             # y = x ^ (x >> 3)
    slli t1, t1, 5
    add  t1, t1, s0             # t1 = &node slot y
    sd   t4, 8(t1)              # payload
    sd   t1, 0(t3)              # previous->next = this
    mv   t3, t1
    addi s6, s6, STEP
    andi s6, s6, 255            # modulo 256
    addi t4, t4, 1
    bne  t4, t5, build
    sd   s0, 0(t3)              # last->next = head: close the circle

    li   s1, PASSES
    li   s2, 0
    li   s3, 0
circuit:
    mv   t0, s0                 # p = head
    li   t1, NODES
hop:
    ld   t2, 8(t0)              # payload
    add  s2, s2, t2
    slli s3, s3, 1              # order-sensitive hash: h = (h << 1) ^ payload
    xor  s3, s3, t2
    ld   t0, 0(t0)              # p = p->next  (depends on the previous hop)
    addi t1, t1, -1
    bnez t1, hop
    li   gp, 1                  # a full circuit must return to the head
    bne  t0, s0, fail
    addi s1, s1, -1
    bnez s1, circuit

    li   gp, 2                  # payload sum over all circuits
    li   t5, 394752
    bne  s2, t5, fail
    li   gp, 3                  # order hash
    li   t5, 0x11f1ee31ee0e104e
    bne  s3, t5, fail

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
nodes:
    .zero 32 * NODES
