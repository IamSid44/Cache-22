# calls.S - a loop that makes six calls per iteration to small leaf functions.
# Shows: the BTB for jumps and returns (every call/ret is a taken jump).
.equ HALT, 0x10000008
.equ ITER, 800

    li   s0, ITER
    li   a0, 1
    la   s1, ftable
loop:
    jal  ra, fn_add             # call site 1: only caller of fn_add
    jal  ra, fn_mix             # call site 2: only caller of fn_mix
    jal  ra, fn_shared          # call site 3: first caller of fn_shared
    jal  ra, fn_fold            # call site 4: only caller of fn_fold
    jal  ra, fn_shared          # call site 5: second caller of fn_shared
    ld   t0, 0(s1)              # function pointer from the table
    jalr ra, 0(t0)              # call site 6: indirect, always fn_ptr
    addi s0, s0, -1
    bnez s0, loop

    li   gp, 1                  # accumulator after all calls
    li   t5, 364591930616250871
    bne  a0, t5, fail

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

fn_add:                         # a0 += 5
    addi a0, a0, 5
    ret
fn_mix:                         # a0 ^= a0 << 1
    slli t1, a0, 1
    xor  a0, a0, t1
    ret
fn_shared:                      # a0 = (a0 ^ 0x1c5) + 3
    xori a0, a0, 0x1c5
    addi a0, a0, 3
    ret
fn_fold:                        # a0 += a0 >> 7
    srli t1, a0, 7
    add  a0, a0, t1
    ret
fn_ptr:                         # a0 -= 9; a0 ^= a0 >> 3   (reached through the table)
    addi a0, a0, -9
    srli t1, a0, 3
    xor  a0, a0, t1
    ret

    .align 3
ftable:
    .dword fn_ptr
