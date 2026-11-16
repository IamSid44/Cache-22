#!/usr/bin/env python3
"""Generate sw/tests/pairs.S.

    python sw/tests/gen_pairs.py
"""

import os

PLACEMENTS = (0, 1, 7)

CHECKS = [
    # ---- registers ----
    ("the second reads the register the first writes",
     ["li   a0, 100"],
     ["li   a0, 5", "addi a1, a0, 1", "add  a2, a0, a1"],
     [("a1", 6), ("a2", 11)]),
    ("both operands of the second come from the first",
     ["li   a0, 100"],
     ["li   a0, 7", "add  a1, a0, a0", "sub  a2, a1, a0"],
     [("a1", 14), ("a2", 7)]),
    ("the second overwrites the register the first reads",
     ["li   a0, 3"],
     ["addi a1, a0, 1", "li   a0, 50", "addi a2, a0, 1"],
     [("a1", 4), ("a0", 50), ("a2", 51)]),
    ("both write the same register; read while in flight and after retirement",
     ["li   a0, 100"],
     ["li   a0, 1", "li   a0, 2", "mv   a1, a0",
      "j    {L}a",          # mispredicted the first time: everything is
      "li   a0, 99",        # discarded and a0 is read from the register file
      "{L}a:", "mv   a2, a0"],
     [("a1", 2), ("a2", 2), ("a0", 2)]),
    ("each reads and writes the same register",
     ["li   a0, 0"],
     ["addi a0, a0, 1", "slli a0, a0, 1", "addi a0, a0, 3", "slli a0, a0, 2",
      "xori a0, a0, 0x55", "addi a0, a0, -7"],
     [("a0", 58)]),
    ("two chains interleaved, then joined",
     ["li   a0, 1", "li   a1, 2"],
     ["addi a0, a0, 10", "addi a1, a1, 20", "slli a0, a0, 1", "slli a1, a1, 1",
      "add  a2, a0, a1", "sub  a3, a1, a0", "add  a4, a2, a3"],
     [("a0", 22), ("a1", 44), ("a2", 66), ("a3", 22), ("a4", 88)]),
    ("x0 as destination and source",
     ["li   a0, 9", "li   a1, 9"],
     ["addi x0, x0, 5", "add  a0, x0, x0", "add  x0, a1, a1", "mv   a2, x0"],
     [("a0", 0), ("a2", 0)]),

    # ---- memory ----
    ("a store whose data comes from its partner",
     ["sd   x0, 0(s0)"],
     ["li   t0, 0x77", "sd   t0, 0(s0)", "ld   t1, 0(s0)"],
     [("t1", 0x77)]),
    ("a store whose address comes from its partner",
     ["sd   x0, 8(s0)", "li   t2, 0x88"],
     ["addi t0, s0, 8", "sd   t2, 0(t0)", "ld   t1, 8(s0)"],
     [("t1", 0x88)]),
    ("a load and the instruction that uses it",
     ["li   t2, 0x99", "sd   t2, 16(s0)"],
     ["ld   t0, 16(s0)", "addi t1, t0, 1", "add  t3, t1, t0"],
     [("t1", 0x9a), ("t3", 0x133)]),
    ("a store and a load of the same place",
     ["li   t2, 0xab", "sd   x0, 24(s0)"],
     ["sd   t2, 24(s0)", "ld   t0, 24(s0)", "lbu  t1, 24(s0)"],
     [("t0", 0xab), ("t1", 0xab)]),
    ("two stores, then two loads",
     ["li   t2, 1", "li   t3, 2"],
     ["sd   t2, 32(s0)", "sd   t3, 40(s0)", "ld   t0, 32(s0)", "ld   t1, 40(s0)",
      "add  t4, t0, t1"],
     [("t0", 1), ("t1", 2), ("t4", 3)]),
    ("two stores to one place",
     ["li   t2, 1", "li   t3, 2"],
     ["sd   t2, 48(s0)", "sd   t3, 48(s0)", "ld   t0, 48(s0)"],
     [("t0", 2)]),

    # ---- control flow ----
    ("a taken branch; its partner is on the wrong path",
     ["li   a0, 5", "sd   x0, 56(s0)"],
     ["beq  x0, x0, {L}t", "li   a0, 99", "sd   a0, 56(s0)", "{L}t:", "ld   t0, 56(s0)"],
     [("a0", 5), ("t0", 0)]),
    ("a branch that is not taken; its partner executes",
     ["li   a0, 5"],
     ["bne  x0, x0, {L}f", "li   a0, 6", "addi a1, a0, 1"],
     [("a0", 6), ("a1", 7)]),
    ("a taken branch as the second of a pair",
     ["li   a0, 5"],
     ["li   a0, 6", "beq  x0, x0, {L}t", "li   a0, 99", "li   a0, 98", "{L}t:"],
     [("a0", 6)]),
    ("a branch decided by its partner (taken)",
     ["li   t0, 0", "li   a0, 5"],
     ["li   t0, 1", "bnez t0, {L}t", "li   a0, 99", "{L}t:"],
     [("a0", 5)]),
    ("a branch decided by its partner (not taken)",
     ["li   t0, 1", "li   a0, 5"],
     ["li   t0, 0", "bnez t0, {L}f", "li   a0, 6"],
     [("a0", 6)]),
    ("a jump; its link register and its wrong-path partner",
     ["li   a0, 5"],
     ["jal  a1, {L}t", "{L}r:", "li   a0, 99", "{L}t:", "la   t0, {L}r"],
     [("a0", 5), "bne  a1, t0, {L}f"]),
    ("a jump as the second of a pair",
     ["li   a0, 5"],
     ["li   a0, 6", "jal  a1, {L}t", "{L}r:", "li   a0, 99", "{L}t:", "la   t0, {L}r"],
     [("a0", 6), "bne  a1, t0, {L}f"]),
    ("an indirect jump whose target comes from its partner",
     ["la   t0, {L}t", "addi t0, t0, -8", "li   a0, 5"],
     ["addi t0, t0, 8", "jalr a1, 0(t0)", "{L}r:", "li   a0, 99", "{L}t:", "la   t1, {L}r"],
     [("a0", 5), "bne  a1, t1, {L}f"]),
    ("a loop of two instructions",
     ["li   t0, 10"],
     ["{L}l:", "addi t0, t0, -1", "bnez t0, {L}l"],
     [("t0", 0)]),
    ("a loop of three instructions",
     ["li   t0, 10", "li   a0, 0"],
     ["{L}l:", "addi a0, a0, 3", "addi t0, t0, -1", "bnez t0, {L}l"],
     [("a0", 30)]),
    ("a loop that carries a value through memory",
     ["li   t0, 10", "sd   x0, 72(s0)"],
     ["{L}l:", "ld   t1, 72(s0)", "addi t1, t1, 3", "sd   t1, 72(s0)",
      "addi t0, t0, -1", "bnez t0, {L}l", "ld   t2, 72(s0)"],
     [("t2", 30)]),

    # ---- traps ----
    ("an illegal instruction; its partner runs once, after the handler",
     ["li   a0, 0", "mv   s6, s5"],
     [".word 0xffffffff", "addi a0, a0, 1", "addi a0, a0, 1"],
     [("a0", 2), ("s2", 2), "addi s6, s6, 1", "bne  s5, s6, {L}f"]),
    ("an illegal instruction as the second of a pair",
     ["li   a0, 0", "mv   s6, s5"],
     ["addi a0, a0, 1", ".word 0xffffffff", "addi a0, a0, 1"],
     [("a0", 2), ("s2", 2), "addi s6, s6, 1", "bne  s5, s6, {L}f"]),
    ("a trap whose partner is skipped by the handler and must leave no trace",
     ["li   a0, 0", "mv   s6, s5", "li   s7, 4"],
     ["ecall", "li   a0, 99", "addi a1, a0, 1"],
     [("a0", 0), ("a1", 1), ("s2", 11), "addi s6, s6, 1", "bne  s5, s6, {L}f"]),
    ("a faulting load; its partner uses the register it did not write",
     ["li   t0, 7", "li   t2, 0x20000", "mv   s6, s5"],
     ["ld   t0, 0(t2)", "addi t1, t0, 1"],
     [("t1", 8), ("s2", 5), "addi s6, s6, 1", "bne  s5, s6, {L}f"]),
    ("a misaligned store whose address comes from its partner",
     ["li   t2, 0xee", "sd   x0, 64(s0)", "mv   s6, s5"],
     ["addi t0, s0, 65", "sd   t2, 0(t0)", "ld   t1, 64(s0)"],
     [("t1", 0), ("s2", 6), "addi s6, s6, 1", "bne  s5, s6, {L}f"]),
    ("two traps side by side",
     ["mv   s6, s5"],
     [".word 0xffffffff", "ebreak", "nop"],
     [("s3", 2), ("s2", 3), "addi s6, s6, 2", "bne  s5, s6, {L}f"]),
    ("a store behind a trap is not written",
     ["li   t2, 0xdd", "sd   x0, 80(s0)", "li   s7, 4", "mv   s6, s5"],
     ["ebreak", "sd   t2, 80(s0)", "ld   t1, 80(s0)"],
     [("t1", 0), ("s2", 3), "addi s6, s6, 1", "bne  s5, s6, {L}f"]),

    # ---- system instructions ----
    ("a CSR write and a read of it",
     ["li   t0, 0x123"],
     ["csrw mscratch, t0", "csrr t1, mscratch", "addi t2, t1, 1"],
     [("t1", 0x123), ("t2", 0x124)]),
    ("a CSR write whose operand comes from its partner",
     ["li   t0, 0"],
     ["li   t0, 0x456", "csrw mscratch, t0", "csrr t1, mscratch"],
     [("t1", 0x456)]),
    ("a CSR read and the instructions that use it",
     ["li   t0, 0x789", "csrw mscratch, t0"],
     ["csrr t1, mscratch", "addi t2, t1, 1", "csrrw t3, mscratch, t2", "csrr t4, mscratch"],
     [("t2", 0x78a), ("t3", 0x789), ("t4", 0x78a)]),
    ("instructions retired two at a time are counted",
     ["li   a0, 0", "li   a1, 0"],
     ["csrr t0, minstret", "addi a0, a0, 1", "addi a1, a1, 1", "addi a0, a0, 1",
      "addi a1, a1, 1", "addi a0, a0, 1", "addi a1, a1, 1", "addi a0, a0, 1",
      "addi a1, a1, 1", "csrr t1, minstret", "sub  t2, t1, t0"],
     [("t2", 9), ("a0", 4), ("a1", 4)]),
]

HEADER = """\
# pairs.S - cases that only matter when two instructions are handled per cycle.
# GENERATED by sw/tests/gen_pairs.py; edit that file, not this one.
# Self-checking: exit code 0 = pass, otherwise the number of the failed check.
.equ HALT, 0x10000008

    la   t0, handler
    csrw mtvec, t0
    la   s0, buf
    li   s2, 0
    li   s3, 0
    li   s5, 0
    li   s7, 0
"""

FOOTER = """\

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

handler:                        # return to the instruction after the trap,
    mv   s3, s2                 # or s7 bytes further on
    csrr s2, mcause
    csrr s4, mepc
    addi s5, s5, 1
    addi t6, s4, 4
    add  t6, t6, s7
    li   s7, 0
    csrw mepc, t6
    mret

    .align 3
buf:
    .dword 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
"""


def emit(line):
    return line if line.endswith(":") else "    " + line


def generate():
    out = [HEADER]
    number = 0
    for title, setup, body, checks in CHECKS:
        for placement in PLACEMENTS:
            number += 1
            label = f"c{number}"

            def fill(text):
                return text.replace("{L}", label + "_")

            out.append(f"    # {number}: {title} (placement {placement})")
            out.append(f"    li   gp, {number}")
            out += [emit(fill(line)) for line in setup]
            out.append(f"    j    {label}")
            out.append("    .align 5")
            out.append(f"{label}:")
            out += ["    nop"] * placement
            out += [emit(fill(line)) for line in body]
            for check in checks:
                if isinstance(check, tuple):
                    register, value = check
                    out.append(f"    li   t6, {value}")
                    out.append(f"    bne  {register}, t6, {label}_f")
                else:
                    out.append(emit(fill(check)))
            out.append(f"    j    {label}_ok")
            out.append(f"{label}_f:")
            out.append("    j    fail")
            out.append(f"{label}_ok:")
            out.append("")
    out.append(FOOTER)
    return "\n".join(out)


if __name__ == "__main__":
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pairs.S")
    with open(path, "w", encoding="ascii", newline="\n") as handle:
        handle.write(generate())
    print(f"wrote {path}: {len(CHECKS)} sequences x {len(PLACEMENTS)} placements")
