#!/usr/bin/env python3
"""rvgen - seeded random RV64I program generator for co-simulation.

    python tools/rvgen.py --seed 7 [--length 300] [--traps] [-o out.S]
"""

import argparse
import random
import sys

POOL = ["a0", "a1", "a2", "a3", "a4", "a5", "t0", "t1"]
BASE, CNT, TMP, JT, SAVE_RA, HALT_REG = "s0", "s1", "t3", "t4", "t5", "t6"

SCRATCH_BYTES = 512
NPTR = 16               # pointer entries in the read-only table
NFUNC = 4               # leaf functions f0..f3
MAX_LENGTH = 6000       # keeps the image comfortably inside 64 KiB

R_OPS = ("add sub sll slt sltu xor srl sra or and "
         "addw subw sllw srlw sraw").split()
I_OPS = "addi slti sltiu xori ori andi addiw".split()
SH64 = "slli srli srai".split()
SH32 = "slliw srliw sraiw".split()
LOADS = {"lb": 1, "lh": 2, "lw": 4, "ld": 8, "lbu": 1, "lhu": 2, "lwu": 4}
STORES = {"sb": 1, "sh": 2, "sw": 4, "sd": 8}
BRANCHES = "beq bne blt bge bltu bgeu".split()

EDGE_IMM = [0, 1, -1, 2047, -2048, 2, -2, 255, 256, -256, 1024, -1024,
            0x555, -0x556, 2046, -2047]
EDGE_VALUES = [0, 1, -1, 2, 0x7fffffff, 0x80000000, 0xffffffff,
               0x8000000000000000, 0x7fffffffffffffff, 0x100000000,
               -0x80000000, 0x7ff, 0x800, 0xfff, 0xffff0000, 0x00000000ffff8000]


# ---- trap mode ---------------------------------------------------------------
CHK, SKIP, TRAPS = "s2", "s7", "s8"
SKIPPED_TAG = "    # skipped"      # marks an instruction that must never execute

# Addresses outside the 64 KiB RAM that are safe to *store* to (never the
# console/halt MMIO words), 8-aligned; also used as loaded "bad pointers".
BAD_PTRS = [0x10000, 0x10008, 0x20000, 0x40000000, 0x7ffffffffffffff8,
            0x8000000000000000, 0xffffffffffffff00, 0x10000010]
NBAD = len(BAD_PTRS)
MMIO_LOAD_ADDRS = [0x10000000, 0x10000008]      # loads there are access faults


def _enc(opc, f3=0, f7=0, rd=5, rs1=6, rs2=7):
    return (f7 << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | opc


def _enc_i(opc, f3, imm, rd=5, rs1=6):
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | opc


# Words that must raise "illegal instruction" on the golden model (checked by
# the unit tests).
ILLEGAL_WORDS = [
    0xffffffff, 0x00000000, 0x00000001, 0x0000000b, 0x00000007, 0x00000053,
    _enc(0x33, 0, 0x01), _enc(0x33, 5, 0x01), _enc(0x33, 1, 0x20),
    _enc(0x33, 7, 0x20), _enc(0x33, 0, 0x40), _enc(0x33, 4, 0x02),
    _enc(0x3B, 0, 0x01), _enc(0x3B, 2, 0x00), _enc(0x3B, 1, 0x20),
    _enc(0x3B, 4, 0x00), _enc(0x3B, 5, 0x40),
    _enc_i(0x1B, 2, 5), _enc_i(0x1B, 4, 5), _enc_i(0x1B, 7, 5),
    _enc_i(0x1B, 1, 0x20 | 3),          # slliw, shamt bit 5 set
    _enc_i(0x1B, 5, 0x20 | 3),          # srliw, shamt bit 5 set
    _enc_i(0x1B, 5, 0x400 | 0x20 | 3),  # sraiw, shamt bit 5 set
    _enc_i(0x1B, 1, 0x400 | 3),         # slliw with the sra bit
    _enc_i(0x13, 1, 0x400 | 3),         # slli with the sra bit
    _enc_i(0x13, 1, 0x800 | 2),         # slli, reserved upper bits
    _enc_i(0x13, 5, 0x200 | 3),         # srli, reserved upper bits
    _enc_i(0x13, 5, 0x600 | 1),         # srai, extra upper bits
    _enc_i(0x03, 7, 8), _enc_i(0x03, 7, 0),
    _enc(0x23, 4), _enc(0x23, 5), _enc(0x23, 6), _enc(0x23, 7),
    _enc(0x63, 2), _enc(0x63, 3),
    _enc_i(0x67, 1, 0), _enc_i(0x67, 5, 8),
    0x00004073, 0x00200073, 0x10200073, 0x00500073, 0x7ff00073,
]

# Instruction-class constants for the CSR constructs.
CSR_READABLE = ["mepc", "mcause", "mtval", "mstatus", "mie", "mip", "misa",
                "minstret", "mscratch", "mhartid", "mvendorid", "marchid",
                "mimpid"]
CSR_RO = ["mhartid", "mvendorid", "marchid", "mimpid"]
CSR_BAD = [0x7c0, 0x7c1, 0x123, 0xc00, 0x320, 0x100, 0xf15, 0x7b0]
# written by the body (never mtvec / mepc / mcycle)
CSR_WRITABLE = ["mscratch", "mscratch", "mscratch", "mcause", "mtval", "mie",
                "mip", "minstret"]


def _hex64(value):
    return f"0x{value & 0xFFFFFFFFFFFFFFFF:x}"


class Gen:
    def __init__(self, seed, length, traps=False):
        self.r = random.Random(seed)
        self.length = length
        self.traps = traps
        self.lines = []
        self.n = 0                  # static instruction count (approximate)
        self.nlabel = 0
        self.recent = []            # most recently written pool registers
        self.ctx = "top"            # "top", "loop" or "func"

    # ---- emission helpers -------------------------------------------------
    def ins(self, text, count=1, tag=""):
        self.lines.append("    " + text + tag)
        self.n += count

    def label(self, name):
        self.lines.append(f"{name}:")

    def comment(self, text):
        self.lines.append(f"    # {text}")

    def newlabel(self, stem="L"):
        self.nlabel += 1
        return f".{stem}{self.nlabel}"

    # ---- register / immediate choice ---------------------------------------
    def wrote(self, reg):
        if reg in POOL:
            self.recent.append(reg)
            del self.recent[:-3]

    def src(self):
        x = self.r.random()
        if x < 0.06:
            return "x0"
        if x < 0.55 and self.recent:
            return self.r.choice(self.recent)
        return self.r.choice(POOL)

    def pool_dst(self):
        """A writable pool register (never x0); often re-uses a recent one."""
        if self.recent and self.r.random() < 0.2:
            rd = self.recent[-1]
        else:
            rd = self.r.choice(POOL)
        self.wrote(rd)
        return rd

    def dst(self):
        if self.r.random() < 0.05:
            return "x0"
        return self.pool_dst()

    def imm12(self):
        if self.r.random() < 0.45:
            return self.r.choice(EDGE_IMM)
        return self.r.randint(-2048, 2047)

    def shamt(self, bits):
        if self.r.random() < 0.6:
            return self.r.choice([0, 1, bits - 1, bits // 2, bits // 2 - 1, bits - 2])
        return self.r.randrange(bits)

    def value(self):
        x = self.r.random()
        if x < 0.4:
            return self.r.choice(EDGE_VALUES)
        if x < 0.7:
            return self.r.getrandbits(64)
        return self.r.getrandbits(32) - (1 << 31)

    def mem_off(self, size):
        """Aligned offset inside the scratch area, edges over-represented."""
        if self.r.random() < 0.15:
            return self.r.choice([0, SCRATCH_BYTES - size])
        return size * self.r.randrange(SCRATCH_BYTES // size)

    # ---- small building blocks ---------------------------------------------
    def alu_any(self, rd=None, a=None, b=None):
        """One random register-writing ALU instruction."""
        rd = rd or self.dst()
        a = a or self.src()
        kind = self.r.random()
        if kind < 0.5:
            self.ins(f"{self.r.choice(R_OPS)} {rd}, {a}, {b or self.src()}")
        elif kind < 0.8:
            self.ins(f"{self.r.choice(I_OPS)} {rd}, {a}, {self.imm12()}")
        elif kind < 0.9:
            self.ins(f"{self.r.choice(SH64)} {rd}, {a}, {self.shamt(64)}")
        else:
            self.ins(f"{self.r.choice(SH32)} {rd}, {a}, {self.shamt(32)}")

    def use_of(self, reg):
        """An instruction that reads `reg` as rs1, rs2 or both."""
        how = self.r.choice(["rs1", "rs2", "both", "imm"])
        rd = self.dst()
        if how == "rs1":
            self.ins(f"{self.r.choice(R_OPS)} {rd}, {reg}, {self.src()}")
        elif how == "rs2":
            self.ins(f"{self.r.choice(R_OPS)} {rd}, {self.src()}, {reg}")
        elif how == "both":
            self.ins(f"{self.r.choice(R_OPS)} {rd}, {reg}, {reg}")
        else:
            self.ins(f"{self.r.choice(I_OPS)} {rd}, {reg}, {self.imm12()}")

    def idx_addr(self, rs, size):
        """Emit TMP = BASE + (rs masked, aligned); return (base reg, offset)."""
        wide = self.r.random() < 0.5
        mask = (0x1ff if wide else 0xff) & ~(size - 1)
        self.ins(f"andi {TMP}, {rs}, {mask}")
        if self.r.random() < 0.5:
            self.ins(f"add {TMP}, {TMP}, {BASE}")
        else:
            self.ins(f"add {TMP}, {BASE}, {TMP}")
        off = 0 if wide else size * self.r.randint(0, 256 // size)
        return TMP, off

    def load(self, rd, op=None):
        op = op or self.r.choice(list(LOADS))
        self.ins(f"{op} {rd}, {self.mem_off(LOADS[op])}({BASE})")

    def store(self, data=None, op=None):
        op = op or self.r.choice(list(STORES))
        data = data or self.src()
        self.ins(f"{op} {data}, {self.mem_off(STORES[op])}({BASE})")

    def cond(self, a=None, b=None):
        """(branch op, rs1, rs2)."""
        op = self.r.choice(BRANCHES)
        x = self.r.random()
        a = a or self.src()
        if b is None:
            if x < 0.25:
                b = a
            elif x < 0.35:
                b = "x0"
            else:
                b = self.src()
        return op, a, b

    def shadow(self, end, n):
        """Instructions that must be squashed when the branch/jump before
        them is taken, and that would visibly corrupt state if they
        executed."""
        for i in range(n):
            if self.traps and self.r.random() < 0.2:
                self.fault(simple_only=True)
                continue
            x = self.r.random()
            if x < 0.30:
                op = self.r.choice(list(STORES))
                self.ins(f"{op} {self.r.choice(POOL)}, {self.mem_off(STORES[op])}({BASE})")
            elif x < 0.55:
                rd = self.r.choice(POOL)
                self.ins(f"addi {rd}, {rd}, {self.imm12() or 7}")
                self.wrote(rd)
            elif x < 0.67:
                rd = self.r.choice(POOL)
                self.ins(f"li {rd}, {_hex64(self.value() | 0x10)}")
                self.wrote(rd)
            elif x < 0.80:
                rd = self.r.choice(POOL)
                self.load(rd)
                self.wrote(rd)
            elif x < 0.92:
                rd = self.r.choice(POOL)
                self.ins(f"{self.r.choice(R_OPS)} {rd}, {self.src()}, {self.src()}")
                self.wrote(rd)
            elif i == n - 1 and x < 0.96:
                self.ins(f"j {end}")
            elif i == n - 1:
                op, a, b = self.cond()
                self.ins(f"{op} {a}, {b}, {end}")
            else:
                self.ins(f"xori {self.r.choice(POOL)}, {self.r.choice(POOL)}, -1")

    def branch_over(self, a=None, b=None):
        """branch + squashable shadow + label; the core of most constructs."""
        end = self.newlabel()
        op, a, b = self.cond(a, b)
        self.ins(f"{op} {a}, {b}, {end}")
        self.shadow(end, self.r.randint(1, 3))
        self.label(end)

    # ---- constructs: straight-line ------------------------------------------
    def c_alu_rr(self):
        self.ins(f"{self.r.choice(R_OPS)} {self.dst()}, {self.src()}, {self.src()}")

    def c_alu_ri(self):
        self.ins(f"{self.r.choice(I_OPS)} {self.dst()}, {self.src()}, {self.imm12()}")

    def c_shift(self):
        if self.r.random() < 0.6:
            self.ins(f"{self.r.choice(SH64)} {self.dst()}, {self.src()}, {self.shamt(64)}")
        else:
            self.ins(f"{self.r.choice(SH32)} {self.dst()}, {self.src()}, {self.shamt(32)}")

    def c_upper(self):
        imm = self.r.choice([0, 1, 0x7ffff, 0x80000, 0xfffff,
                             self.r.randrange(1 << 20)])
        self.ins(f"{self.r.choice(['lui', 'auipc'])} {self.dst()}, {imm}")

    def c_li(self):
        self.ins(f"li {self.pool_dst()}, {_hex64(self.value())}")

    def c_load(self):
        self.load(self.dst())

    def c_store(self):
        self.store()

    def c_load_idx(self):
        op = self.r.choice(list(LOADS))
        base, off = self.idx_addr(self.src(), LOADS[op])
        self.ins(f"{op} {self.dst() if self.r.random() < 0.9 else TMP}, {off}({base})")

    def c_store_idx(self):
        op = self.r.choice(list(STORES))
        base, off = self.idx_addr(self.src(), STORES[op])
        self.ins(f"{op} {self.src()}, {off}({base})")

    # ---- constructs: data hazards -------------------------------------------
    def c_load_use(self):
        """Load, then immediately consume the loaded register."""
        rd = self.pool_dst()
        self.load(rd)
        mode = self.r.choice(["use", "use", "store_data", "load_index",
                              "store_index", "branch", "branch"])
        if mode == "use":
            self.use_of(rd)
        elif mode == "store_data":
            self.store(data=rd)
        elif mode == "load_index":
            op = self.r.choice(list(LOADS))
            base, off = self.idx_addr(rd, LOADS[op])
            self.ins(f"{op} {self.dst()}, {off}({base})")
        elif mode == "store_index":
            op = self.r.choice(list(STORES))
            base, off = self.idx_addr(rd, STORES[op])
            self.ins(f"{op} {self.src()}, {off}({base})")
        else:
            self.branch_over(a=rd)

    def c_load_branch(self):
        """Branch right after a load, with the load as rs1 or rs2."""
        rd = self.pool_dst()
        self.load(rd)
        other = self.src()
        if self.r.random() < 0.5:
            self.branch_over(a=rd, b=other)
        else:
            self.branch_over(a=other, b=rd)

    def c_ptr_chase(self):
        """Load a pointer, then use it as the base of the next access."""
        x = self.r.choice([r for r in POOL])
        self.ins(f"ld {x}, {SCRATCH_BYTES + 8 * self.r.randrange(NPTR)}({BASE})")
        self.wrote(x)
        mode = self.r.choice(["load", "store", "self", "load_use"])
        # table pointers are 8-aligned, below scratch+256; offsets stay < 256
        if mode == "store":
            op = self.r.choice(list(STORES))
            sz = STORES[op]
            self.ins(f"{op} {self.src()}, {sz * self.r.randrange(256 // sz)}({x})")
        else:
            op = self.r.choice(list(LOADS))
            sz = LOADS[op]
            y = x if mode == "self" else self.pool_dst()
            self.ins(f"{op} {y}, {sz * self.r.randrange(256 // sz)}({x})")
            self.wrote(y)
            if mode in ("self", "load_use"):
                self.use_of(y)

    def c_store_load(self):
        """Store immediately followed by a load overlapping (or near) it."""
        sop = self.r.choice(list(STORES))
        lop = self.r.choice(list(LOADS))
        s, l = STORES[sop], LOADS[lop]
        if self.r.random() < 0.25:
            # data-dependent address; 8-aligned so the block is [TMP, TMP+8)
            self.ins(f"andi {TMP}, {self.src()}, {0x1f8}")
            self.ins(f"add {TMP}, {TMP}, {BASE}")
            base, block = TMP, 0
        else:
            base, block = BASE, 8 * self.r.randrange(SCRATCH_BYTES // 8)
        soff = s * self.r.randrange(8 // s)
        if self.r.random() < 0.8:                       # overlapping
            if l <= s:
                loff = soff + l * self.r.randrange(s // l)
            else:
                loff = soff & ~(l - 1)
        else:
            loff = l * self.r.randrange(8 // l)
        self.ins(f"{sop} {self.src()}, {block + soff}({base})")
        rd = self.pool_dst()
        self.ins(f"{lop} {rd}, {block + loff}({base})")
        if self.r.random() < 0.5:
            self.use_of(rd)

    def c_same_rs(self):
        """op q, p, p where p was written by the previous instruction."""
        p = self.pool_dst()
        if self.r.random() < 0.3:
            self.load(p)
        else:
            self.alu_any(rd=p)
        q = self.dst() if self.r.random() < 0.7 else p
        self.ins(f"{self.r.choice(R_OPS)} {q}, {p}, {p}")

    def c_double_write(self):
        """Two writes to one register, then a read of it."""
        p = self.pool_dst()
        if self.r.random() < 0.4:
            self.load(p)
        else:
            self.alu_any(rd=p)
        if self.r.random() < 0.3:
            self.load(p)
        else:
            self.alu_any(rd=p)
        self.use_of(p)

    def c_alu_branch(self):
        """Branch whose operand was produced by the previous instruction."""
        p = self.pool_dst()
        self.alu_any(rd=p)
        self.branch_over(a=p, b=p if self.r.random() < 0.3 else None)

    # ---- constructs: control flow -------------------------------------------
    def c_branch_skip(self):
        self.branch_over()

    def c_b2b(self):
        """Back-to-back branches sharing targets."""
        l1, l2 = self.newlabel(), self.newlabel()
        op, a, b = self.cond()
        self.ins(f"{op} {a}, {b}, {l1}")
        op, a, b = self.cond()
        self.ins(f"{op} {a}, {b}, {l2}")
        self.shadow(l1, self.r.randint(1, 2))
        self.label(l1)
        op, a, b = self.cond()
        self.ins(f"{op} {a}, {b}, {l2}")
        self.shadow(l2, self.r.randint(1, 2))
        self.label(l2)

    def link_reg(self):
        x = self.r.random()
        if x < 0.4:
            return "x0"
        if x < 0.8 or self.ctx == "func":
            return self.pool_dst()
        return "ra"

    def c_jal_skip(self):
        end = self.newlabel()
        rd = self.link_reg()
        self.ins(f"jal {rd}, {end}")
        self.shadow(end, self.r.randint(1, 3))
        self.label(end)
        if rd != "x0" and rd != "ra" and self.r.random() < 0.6:
            self.use_of(rd)

    def la_jalr(self, target, rd, treg):
        """la treg, target+k ; jalr rd, -k+odd(treg)."""
        k = self.r.choice([0, 0, 4, -4, 8, 64, -64, 1000, -1000, 2044, -2044,
                           self.r.randint(-2046, 2046)])
        odd = 1 if self.r.random() < 0.2 else 0
        if k:
            self.ins(f"la {treg}, {target} + {k}", 2)
        else:
            self.ins(f"la {treg}, {target}", 2)
        self.ins(f"jalr {rd}, {-k + odd}({treg})")

    def c_jalr_skip(self):
        end = self.newlabel()
        treg = JT if self.r.random() < 0.6 else self.r.choice(POOL)
        x = self.r.random()
        if x < 0.25:
            rd = "x0"
        elif x < 0.55:
            rd = treg                       # rd == rs1
        elif x < 0.85 or self.ctx == "func":
            rd = self.r.choice(POOL)
        else:
            rd = "ra"
        self.la_jalr(end, rd, treg)
        for r in (rd, treg):
            self.wrote(r)
        self.shadow(end, self.r.randint(1, 3))
        self.label(end)
        if rd in POOL and self.r.random() < 0.5:
            self.use_of(rd)

    def c_call(self):
        f = f"f{self.r.randrange(NFUNC)}"
        x = self.r.random()
        if x < 0.35:
            self.ins(f"{self.r.choice(['jal ra,', 'call'])} {f}")
        elif x < 0.65:
            self.la_jalr(f, "ra", JT)
        else:
            idx = NPTR + int(f[1:])
            self.ins(f"ld {JT}, {SCRATCH_BYTES + 8 * idx}({BASE})")
            self.ins(f"jalr ra, 0({JT})")
        if self.r.random() < 0.6:
            self.use_of(self.r.choice(self.recent or POOL))

    def c_loop(self):
        """Counted loop: bottom branch on the reserved counter s1."""
        top = self.newlabel("loop")
        self.ins(f"li {CNT}, {self.r.randint(3, 10)}")
        self.label(top)
        self.ctx = "loop"
        for _ in range(self.r.randint(3, 10)):
            self.construct()
        self.ctx = "top"
        self.ins(f"addi {CNT}, {CNT}, -1")
        if self.r.random() < 0.3:
            self.alu_any()
        self.ins(self.r.choice([f"bnez {CNT}, {top}",
                                f"bne {CNT}, x0, {top}",
                                f"blt x0, {CNT}, {top}"]))

    def c_nop(self):
        self.ins("nop")

    # ---- trap mode: fault building blocks -----------------------------------
    FAULTS = [
        # (weight, name, always traps, usable inside a shadow)
        (1, "ecall", True, True), (1, "ebreak", True, True),
        (4, "illegal", True, True), (3, "mis_load", True, True),
        (3, "mis_store", True, True), (3, "oob_load", True, True),
        (2, "oob_store", True, True), (2, "csr_bad", True, True),
        (2, "jalr_mis", True, False), (1, "jal_mis", True, False),
        (2, "br_mis_taken", True, False), (1, "br_mis_nt", False, False),
        (1, "br_mis_maybe", False, False),
    ]

    def skipped(self):
        """One instruction the handler must skip; its wrongful execution is
        made visible by the observer instruction that follows it."""
        x = self.r.random()
        if x < 0.4:
            op = self.r.choice(list(STORES))
            off = self.mem_off(STORES[op])
            self.ins(f"{op} {self.r.choice(POOL)}, {off}({BASE})", tag=SKIPPED_TAG)
            self.ins(f"ld {self.pool_dst()}, {off & ~7}({BASE})")
        elif x < 0.7:
            p = self.r.choice(POOL)
            self.ins(f"addi {p}, {p}, {self.r.choice([1, 5, -3, 100, 2047])}",
                     tag=SKIPPED_TAG)
            self.wrote(p)
            self.use_of(p)
        else:
            self.ins(f"csrrw x0, mscratch, {self.r.choice(POOL)}", tag=SKIPPED_TAG)
            self.ins(f"csrr {self.pool_dst()}, mscratch")

    def fault(self, simple_only=False, skip=None, kind=None):
        """A fault, optionally with the skip mechanism armed (li s7, 4 before
        it and a to-be-skipped instruction right behind it)."""
        if kind is not None:
            entry = next(f for f in self.FAULTS if f[1] == kind)
        else:
            table = [f for f in self.FAULTS if f[3] or not simple_only]
            entry = self.r.choices(table, [f[0] for f in table])[0]
        _, name, always, _ = entry
        if skip is None:
            skip = self.r.random() < 0.3
        skip = skip and always
        if skip:
            self.ins(f"li {SKIP}, 4")
        tail = getattr(self, "f_" + name)()
        if skip:
            self.skipped()
        if tail:
            tail()

    def close(self, end):
        self.shadow(end, self.r.randint(1, 3))
        self.label(end)

    def f_ecall(self):
        self.ins("ecall")

    def f_ebreak(self):
        self.ins("ebreak")

    def f_illegal(self):
        self.ins(f".word 0x{self.r.choice(ILLEGAL_WORDS):08x}")

    def f_mis_load(self):
        op = self.r.choice(["lh", "lhu", "lw", "lwu", "ld"])
        size = LOADS[op]
        k = self.r.randint(1, size - 1)
        if self.r.random() < 0.5:
            off = size * self.r.randrange(SCRATCH_BYTES // size - 1) + k
            self.ins(f"{op} {self.dst()}, {off}({BASE})")
        else:
            self.ins(f"andi {TMP}, {self.src()}, {0x1ff & ~(size - 1)}")
            self.ins(f"add {TMP}, {TMP}, {BASE}")
            self.ins(f"{op} {self.dst()}, {k}({TMP})")

    def f_mis_store(self):
        op = self.r.choice(["sh", "sw", "sd"])
        size = STORES[op]
        k = self.r.randint(1, size - 1)
        if self.r.random() < 0.5:
            off = size * self.r.randrange(SCRATCH_BYTES // size - 1) + k
            self.ins(f"{op} {self.src()}, {off}({BASE})")
        else:
            self.ins(f"andi {TMP}, {self.src()}, {0x1ff & ~(size - 1)}")
            self.ins(f"add {TMP}, {TMP}, {BASE}")
            self.ins(f"{op} {self.src()}, {k}({TMP})")

    def oob_access(self, sizes, addrs):
        """li TMP with an address outside RAM; returns (op, offset)."""
        op = self.r.choice(list(sizes))
        size = sizes[op]
        off = size * self.r.randint(0, 3)
        if size > 1 and self.r.random() < 0.2:
            off += self.r.randint(1, size - 1)
        self.ins(f"li {TMP}, {_hex64(self.r.choice(addrs) - off)}")
        return op, off

    def f_oob_load(self):
        op, off = self.oob_access(LOADS, BAD_PTRS + MMIO_LOAD_ADDRS)
        self.ins(f"{op} {self.dst()}, {off}({TMP})")

    def f_oob_store(self):
        op, off = self.oob_access(STORES, BAD_PTRS)
        self.ins(f"{op} {self.src()}, {off}({TMP})")

    def f_csr_bad(self):
        form = self.r.choice(self.CSR_FORMS)
        if self.r.random() < 0.5:
            csr = self.r.choice(CSR_RO)         # a write attempt
            if form.endswith("i"):
                operand = self.r.randint(0 if form == "csrrwi" else 1, 31)
            else:
                operand = self.r.choice(POOL)   # rs1 field is never x0
        else:
            csr = f"0x{self.r.choice(CSR_BAD):x}"   # unimplemented CSR
            operand = self.r.randint(0, 31) if form.endswith("i") else self.src()
        self.ins(f"{form} {self.dst()}, {csr}, {operand}")

    def f_jalr_mis(self):
        end = self.newlabel()
        treg = JT if self.r.random() < 0.6 else self.r.choice(POOL)
        x = self.r.random()
        if x < 0.2:
            rd = "x0"
        elif x < 0.45:
            rd = treg                       # rd == rs1
        elif x < 0.85 or self.ctx == "func":
            rd = self.r.choice(POOL)        # must keep its old value
        else:
            rd = "ra"
        self.la_jalr(f"{end} + 2", rd, treg)
        self.wrote(treg)
        return lambda: self.close(end)

    def f_jal_mis(self):
        end = self.newlabel()
        self.ins(f"jal {self.link_reg()}, {end} + 2")
        return lambda: self.close(end)

    def f_br_mis_taken(self):
        end = self.newlabel()
        reg = self.src()
        self.ins(f"{self.r.choice(['beq', 'bge', 'bgeu'])} {reg}, {reg}, {end} + 2")
        return lambda: self.close(end)

    def f_br_mis_nt(self):
        end = self.newlabel()
        reg = self.src()
        self.ins(f"{self.r.choice(['bne', 'blt', 'bltu'])} {reg}, {reg}, {end} + 2")
        return lambda: self.close(end)

    def f_br_mis_maybe(self):
        end = self.newlabel()
        op, a, b = self.cond()
        self.ins(f"{op} {a}, {b}, {end} + 2")
        return lambda: self.close(end)

    # ---- trap mode: constructs --------------------------------------------------
    def c_fault(self):
        self.fault()

    def c_fault_pair(self):
        """Two faults back to back."""
        self.fault(simple_only=True)
        self.fault(simple_only=True)

    def c_fault_order(self):
        """A late-detected memory fault immediately followed by an early one
        (illegal word / ecall / bad CSR): the older one must trap first."""
        self.fault(kind=self.r.choice(["oob_load", "mis_load", "oob_store",
                                       "mis_store"]), skip=False)
        self.fault(kind=self.r.choice(["illegal", "ecall", "csr_bad"]))

    def store_at(self, off8):
        op = self.r.choice(list(STORES))
        size = STORES[op]
        off = off8 + size * self.r.randrange(8 // size)
        self.ins(f"{op} {self.src()}, {off}({BASE})")

    def c_store_fault(self):
        """Store before a fault takes effect; a store after it waits for the
        handler (or is skipped); read the slot back afterwards."""
        off = self.mem_off(8)
        self.store_at(off)
        self.fault(simple_only=True)
        if self.r.random() < 0.7:
            self.store_at(off)
        self.ins(f"ld {self.pool_dst()}, {off}({BASE})")

    def c_fault_shadow(self):
        """Faults in the shadow of a branch/jump that is always taken: they
        must be squashed and must not trap."""
        end = self.newlabel()
        x = self.r.random()
        if x < 0.5:
            reg = self.src()
            self.ins(f"{self.r.choice(['beq', 'bge', 'bgeu'])} {reg}, {reg}, {end}")
        elif x < 0.75:
            self.ins(f"jal {self.link_reg()}, {end}")
        else:
            self.la_jalr(end, self.r.choice(["x0", JT, "a2"]), JT)
        for _ in range(self.r.randint(1, 3)):
            self.fault(simple_only=True)
        self.label(end)

    def c_fault_ptr(self):
        """Load a pointer and use it, immediately, in a faulting access."""
        x = self.r.choice(POOL)
        bad = self.r.random() < 0.6
        idx = NPTR + NFUNC + self.r.randrange(NBAD) if bad else self.r.randrange(NPTR)
        self.ins(f"ld {x}, {SCRATCH_BYTES + 8 * idx}({BASE})")
        self.wrote(x)
        if self.r.random() < 0.5:
            op = self.r.choice(list(LOADS) if bad else ["lh", "lw", "ld", "lhu", "lwu"])
            size = LOADS[op]
        else:
            op = self.r.choice(list(STORES) if bad else ["sh", "sw", "sd"])
            size = STORES[op]
        if bad:                              # access fault (or misaligned)
            off = size * self.r.randint(0, 3)
            if size > 1 and self.r.random() < 0.25:
                off += self.r.randint(1, size - 1)
        else:                                # always misaligned
            off = size * self.r.randrange(256 // size - 1) + self.r.randint(1, size - 1)
        if op in LOADS:
            self.ins(f"{op} {self.dst()}, {off}({x})")
        else:
            self.ins(f"{op} {self.src()}, {off}({x})")

    # ---- trap mode: CSR constructs ----------------------------------------------
    CSR_FORMS = ["csrrw", "csrrs", "csrrc", "csrrwi", "csrrsi", "csrrci"]

    def csr_use(self, rd):
        mode = self.r.choice(["use", "use", "store", "branch"])
        if mode == "use":
            self.use_of(rd)
        elif mode == "store":
            self.store(data=rd)
        else:
            self.branch_over(a=rd)

    def csr_op(self, csr):
        """One random-form CSR instruction on `csr`; returns its rd."""
        form = self.r.choice(self.CSR_FORMS)
        if form.endswith("i"):
            rd = self.dst()
            zimm = self.r.choice([0, 1, 8, 31, self.r.randint(0, 31)])
            self.ins(f"{form} {rd}, {csr}, {zimm}")
        else:
            rs = self.src()
            rd = rs if (rs != "x0" and self.r.random() < 0.25) else self.dst()
            self.ins(f"{form} {rd}, {csr}, {rs}")
        return rd

    def c_csr_scratch(self):
        """Data flowing through mscratch (rd == rs1, rd == x0, rs1 == x0...)."""
        rd = self.csr_op("mscratch")
        if rd in POOL and self.r.random() < 0.5:
            self.csr_use(rd)

    def c_csr_read(self):
        """Pure read of a CSR followed by a use of the result."""
        csr = self.r.choice(CSR_READABLE)
        rd = self.pool_dst()
        form = self.r.choice(["csrr {0}, {1}", "csrrs {0}, {1}, x0",
                              "csrrc {0}, {1}, x0", "csrrsi {0}, {1}, 0",
                              "csrrci {0}, {1}, 0"])
        self.ins(form.format(rd, csr))
        if self.r.random() < 0.8:
            self.csr_use(rd)

    def c_csr_wr(self):
        """Write a CSR and read the same CSR straight back."""
        csr = self.r.choice(CSR_WRITABLE + ["mstatus"])
        if csr == "mstatus":
            self.ins(self.r.choice(["csrsi mstatus, 8", "csrci mstatus, 8",
                                    f"csrrw x0, mstatus, {self.src()}"]))
        else:
            self.csr_op(csr)
        rd = self.pool_dst()
        self.ins(f"csrr {rd}, {csr}")
        if self.r.random() < 0.5:
            self.csr_use(rd)

    def c_mstatus(self):
        """Toggle mstatus.MIE and look at the MIE/MPIE stack."""
        rd = self.dst()
        self.ins(self.r.choice(["csrsi mstatus, 8", "csrci mstatus, 8",
                                f"csrrsi {rd}, mstatus, 8",
                                f"csrrci {rd}, mstatus, 8"]))
        rd = self.pool_dst()
        self.ins(f"csrr {rd}, mstatus")
        self.csr_use(rd)

    # ---- construct table -----------------------------------------------------
    # (weight, method, contexts in which it may appear: T=top L=loop F=function)
    TABLE = [
        (10, "c_alu_rr", "TLF"), (8, "c_alu_ri", "TLF"), (5, "c_shift", "TLF"),
        (3, "c_upper", "TLF"), (2, "c_li", "TLF"), (1, "c_nop", "TLF"),
        (5, "c_load", "TLF"), (5, "c_store", "TLF"),
        (3, "c_load_idx", "TLF"), (3, "c_store_idx", "TLF"),
        (8, "c_load_use", "TLF"), (4, "c_ptr_chase", "TLF"),
        (6, "c_store_load", "TLF"), (4, "c_same_rs", "TLF"),
        (4, "c_double_write", "TLF"), (3, "c_alu_branch", "TLF"),
        (8, "c_branch_skip", "TLF"), (4, "c_load_branch", "TLF"),
        (4, "c_b2b", "TLF"), (3, "c_jal_skip", "TLF"),
        (5, "c_jalr_skip", "TLF"), (4, "c_call", "TL"), (18, "c_loop", "T"),
    ]
    # extra constructs in trap mode
    TRAP_TABLE = [
        (14, "c_fault", "TLF"), (4, "c_fault_pair", "TLF"),
        (4, "c_fault_order", "TLF"), (4, "c_store_fault", "TLF"),
        (5, "c_fault_shadow", "TLF"), (5, "c_fault_ptr", "TLF"),
        (6, "c_csr_scratch", "TLF"), (6, "c_csr_read", "TLF"),
        (5, "c_csr_wr", "TLF"), (3, "c_mstatus", "TLF"),
    ]

    def construct(self):
        flag = {"top": "T", "loop": "L", "func": "F"}[self.ctx]
        table = self.TABLE + self.TRAP_TABLE if self.traps else self.TABLE
        items = [(w, m) for w, m, c in table if flag in c]
        method = self.r.choices([m for _, m in items], [w for w, _ in items])[0]
        getattr(self, method)()

    # ---- program assembly ----------------------------------------------------
    def function(self, i):
        self.label(f"f{i}")
        self.ctx = "func"
        via_save = self.r.random() < 0.3
        if via_save:
            self.ins(f"mv {SAVE_RA}, ra")
        for _ in range(self.r.randint(2, 6)):
            self.construct()
        ret = self.r.choice(["ret", "ret", "tweak", "link"])
        if via_save:
            self.ins(f"jr {SAVE_RA}")
        elif ret == "tweak":                # ra written just before the jalr
            self.ins("addi ra, ra, 4")
            self.ins("addi ra, ra, -4")
            self.ins("ret")
        elif ret == "link":                 # non-x0 link register on a return
            self.ins(f"jalr {self.r.choice(POOL)}, 0(ra)")
        else:
            self.ins("ret")
        self.ctx = "top"

    def handler(self):
        """Resume at mepc + 4 + s7 (s7 = extra skip, cleared)."""
        self.comment("trap handler")
        self.label("handler")
        self.ins("csrr s3, mcause")
        self.ins("csrr s4, mtval")
        self.ins("csrr s5, mepc")
        self.ins("csrr s6, mstatus")
        self.ins(f"add {CHK}, {CHK}, s3")
        self.ins(f"xor {CHK}, {CHK}, s4")
        self.ins(f"add {CHK}, {CHK}, s5")
        self.ins(f"xor {CHK}, {CHK}, s6")
        self.ins(f"addi {TRAPS}, {TRAPS}, 1")
        self.ins("addi s5, s5, 4")
        self.ins(f"add s5, s5, {SKIP}")
        self.ins(f"li {SKIP}, 0")
        self.ins("csrw mepc, s5")
        self.ins("mret")

    def program(self):
        r = self.r
        self.lines.append("# rvgen: random RV64I stress program (generated, do not edit)")
        self.lines.append(".equ HALT, 0x10000008")
        self.ins(f"la {BASE}, scratch", 2)
        if self.traps:
            self.comment("install the trap handler; clear its state registers")
            self.ins("la s3, handler", 2)
            self.ins("csrw mtvec, s3")
            for reg in (CHK, SKIP, TRAPS):
                self.ins(f"li {reg}, 0")
        for reg in POOL:
            self.ins(f"li {reg}, {_hex64(self.value())}")
            self.wrote(reg)
        while self.n < self.length:
            self.construct()
        self.lines.append("")
        self.comment("halt: store exit code 0, then spin")
        self.ins(f"li {HALT_REG}, HALT")
        self.ins(f"sd x0, 0({HALT_REG})")
        self.label("halt_loop")
        self.ins("j halt_loop")
        self.lines.append("")
        for i in range(NFUNC):
            self.function(i)
        self.lines.append("")
        if self.traps:
            self.handler()
            self.lines.append("")
        self.lines.append("    .align 3")
        self.label("scratch")
        for _ in range(SCRATCH_BYTES // 8):
            v = r.choice(EDGE_VALUES) if r.random() < 0.25 else r.getrandbits(64)
            self.lines.append(f"    .dword {_hex64(v)}")
        for _ in range(NPTR):                # read-only: pointers into scratch
            self.lines.append(f"    .dword scratch + {8 * r.randrange(32)}")
        for i in range(NFUNC):               # read-only: function entry points
            self.lines.append(f"    .dword f{i}")
        if self.traps:                       # read-only: pointers outside RAM
            for addr in BAD_PTRS:
                self.lines.append(f"    .dword {_hex64(addr)}")
        return "\n".join(self.lines) + "\n"


def generate(seed, length=300, traps=False):
    """Return assembly source for a random program (deterministic per seed)."""
    if not 1 <= length <= MAX_LENGTH:
        raise ValueError(f"length must be in 1..{MAX_LENGTH}")
    return Gen(seed, length, traps).program()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Random RV64I program generator")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--length", type=int, default=300,
                        help="approximate static instructions in the random body")
    parser.add_argument("--traps", action="store_true",
                        help="add a trap handler, faulting instructions and CSR traffic")
    parser.add_argument("-o", "--output", help="write here instead of stdout")
    args = parser.parse_args(argv)
    text = generate(args.seed, args.length, args.traps)
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
