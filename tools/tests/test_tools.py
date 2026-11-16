"""Unit tests for the assembler (rvasm) and the golden model (rvsim)."""

import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import rvasm  # noqa: E402
import rvsim  # noqa: E402


def words(source):
    image, _, _ = rvasm.assemble(source)
    return [int.from_bytes(image[i:i + 4], "little") for i in range(0, len(image), 4)]


def run(source, max_instructions=10_000):
    image, _, symbols = rvasm.assemble(source)
    trace = io.StringIO()
    sim = rvsim.Simulator(image, trace=trace)
    outcome = sim.run(max_instructions)
    return sim, outcome, trace.getvalue().splitlines(), symbols


HALT = """
    li t6, 0x10000008
    sd a0, 0(t6)
"""


class EncodingTests(unittest.TestCase):
    KNOWN = [
        ("addi x5, x0, 63", 0x03F00293),
        ("add x5, x5, x5", 0x005282B3),
        ("sub x6, x0, x5", 0x40500333),
        ("sub a0, a0, a1", 0x40B50533),
        ("xor a0, a0, a1", 0x00B54533),
        ("sd x6, 17(x0)", 0x006038A3),
        ("sd ra, 8(sp)", 0x00113423),
        ("ld a0, 8(sp)", 0x00813503),
        ("lw a0, 0(a1)", 0x0005A503),
        ("lui a0, 0x12345", 0x12345537),
        ("addiw a0, a0, 1", 0x0015051B),
        ("slli a0, a0, 32", 0x02051513),
        ("srai a0, a0, 1", 0x40155513),
        ("nop", 0x00000013),
        ("ret", 0x00008067),
        ("ecall", 0x00000073),
        ("ebreak", 0x00100073),
        ("mret", 0x30200073),
        ("li a0, 1", 0x00100513),
        ("csrrw x0, mtvec, t0", 0x30529073),
        ("csrr a0, mcause", 0x34202573),
    ]

    def test_known_encodings(self):
        for text, expected in self.KNOWN:
            with self.subTest(text):
                self.assertEqual(words(text), [expected])

    def test_branch_and_jump_offsets(self):
        self.assertEqual(words("beq a0, a1, t\nnop\nt:")[0], 0x00B50463)   # +8
        self.assertEqual(words("t: nop\nbne a0, x0, t")[1], 0xFE051EE3)    # -4
        self.assertEqual(words("jal ra, t\nnop\nnop\nnop\nt:")[0], 0x010000EF)  # +16
        self.assertEqual(words("t: j t")[0], 0x0000006F)

    def test_li_values(self):
        """li must produce the right value for awkward constants."""
        for value in [0, 1, -1, 2047, 2048, -2048, -2049, 0x7FF, 0x800, 0xFFF,
                      0x7FFFFFFF, 0x80000000, -0x80000000, 0x7FFFF800,
                      0xFFFFFFFF, 0x100000000, 0x123456789ABCDEF0,
                      0x7FFFFFFFFFFFFFFF, -0x8000000000000000,
                      0xFFFFFFFFFFFFFFFF, 0x8000000000000001, 0xDEADBEEFCAFEF00D]:
            with self.subTest(hex(value)):
                sim, outcome, _, _ = run(f"li a1, {value}\nli a0, 0\n{HALT}")
                self.assertEqual(outcome, "halt")
                self.assertEqual(sim.x[11], value & rvsim.MASK64)

    def test_labels_named_like_python_keywords(self):
        self.assertEqual(words("pass: j pass")[0], 0x0000006F)

    def test_errors_carry_line_numbers(self):
        with self.assertRaises(rvasm.AsmError) as ctx:
            rvasm.assemble("nop\naddi a0, a0, 5000\n")
        self.assertEqual(ctx.exception.lineno, 2)
        for bad in ["frobnicate a0", "addi a0, a0", "add a0, a1, q9",
                    "beq a0, a1, nowhere", "slli a0, a0, 64", "x: nop\nx: nop"]:
            with self.subTest(bad):
                with self.assertRaises(rvasm.AsmError):
                    rvasm.assemble(bad)


class SimulatorTests(unittest.TestCase):
    def test_trace_format(self):
        _, _, trace, _ = run("""
            li   a1, 0x100
            li   a2, -2
            sd   a2, 8(a1)
            ld   a3, 8(a1)
            beq  a2, a3, skip
            nop
        skip:
            .word 0xffffffff
        """ + "li a0, 0\n" + HALT)
        self.assertEqual(trace[0], "C 0000000000000000 10000593 x11=0000000000000100")
        self.assertEqual(trace[2], "C 0000000000000008 00c5b423 "
                                   "m[0000000000000108,8]=fffffffffffffffe")
        self.assertEqual(trace[3], "C 000000000000000c 0085b683 x13=fffffffffffffffe")
        self.assertEqual(trace[4], "C 0000000000000010 00d60463")          # no state change
        self.assertEqual(trace[5], "T 0000000000000018 ffffffff cause=2 "
                                   "tval=00000000ffffffff")

    def test_trap_is_precise(self):
        """A faulting load changes nothing but the trap CSRs and the pc."""
        sim, outcome, _, symbols = run("""
            la   t0, handler
            csrw mtvec, t0
            li   a1, 5
            li   t1, 0x101
        fault:
            ld   a1, 0(t1)
            addi a1, a1, 1          # must not run before the handler
        handler:
            li   a0, 0
        """ + HALT)
        self.assertEqual(outcome, "halt")
        self.assertEqual(sim.x[11], 5)
        self.assertEqual(sim.csr[rvsim.CSR_MEPC], symbols["fault"])
        self.assertEqual(sim.csr[rvsim.CSR_MCAUSE], rvsim.CAUSE_LOAD_MISALIGNED)
        self.assertEqual(sim.csr[rvsim.CSR_MTVAL], 0x101)
        self.assertEqual(sim.trap_count, 1)

    def test_exit_code_and_console(self):
        sim, outcome, _, _ = run("""
            li t0, 0x10000000
            li t1, 0x4f
            sb t1, 0(t0)
            li t1, 0x4b
            sb t1, 0(t0)
            li a0, 7
        """ + HALT)
        self.assertEqual(outcome, "halt")
        self.assertEqual(sim.exit_code, 7)
        self.assertEqual(bytes(sim.console), b"OK")

    def test_minstret_write_does_not_disturb_the_statistic(self):
        """minstret is a register the program can write; the count of
        instructions the simulator reports must not follow it."""
        sim, outcome, _, _ = run("""
            li   t0, 123456789
            csrw minstret, t0
            csrr a1, minstret
            li   a0, 0
        """ + HALT)
        self.assertEqual(outcome, "halt")
        self.assertEqual(sim.x[11], 123456790)      # the write itself retired
        self.assertLess(sim.retired, 20)
        self.assertGreater(sim.instret, 123456789)

    def test_memory_hash(self):
        """The checksum of memory that the RTL testbench also computes: start
        value, then for each little-endian doubleword xor and multiply."""
        self.assertEqual(rvsim.memory_hash(b""), 0xCBF29CE484222325)
        one = (0xCBF29CE484222325 ^ 0x0807060504030201) * 0x100000001B3 % 2**64
        self.assertEqual(rvsim.memory_hash(bytes(range(1, 9))), one)
        # Every byte matters, and so does its place.
        image = bytearray(64)
        blank = rvsim.memory_hash(image)
        image[40] = 1
        moved = bytearray(64)
        moved[41] = 1
        self.assertEqual(len({blank, rvsim.memory_hash(image), rvsim.memory_hash(moved)}), 3)
        # A program's stores end up in it.
        sim, _, _, _ = run("li a1, 0x200\nli a2, 77\nsd a2, 0(a1)\nli a0, 0\n" + HALT)
        self.assertEqual(sim.mem[0x200], 77)
        self.assertNotEqual(rvsim.memory_hash(sim.mem),
                            rvsim.memory_hash(sim.mem[:0x200] + bytes(8) + sim.mem[0x208:]))

    def test_runaway_program_times_out(self):
        _, outcome, _, _ = run("spin: j spin", max_instructions=100)
        self.assertEqual(outcome, "timeout")

    def test_reserved_encodings_are_illegal(self):
        for word in [0x00000000, 0xFFFFFFFF,
                     0x02051533,        # funct7=1 on OP (that is the M extension)
                     0x4000C533,        # funct7=0x20 with xor
                     0x0205151B,        # slliw with shamt[5] set
                     0x00007003,        # load with funct3=7
                     0x00004023,        # store with funct3=4
                     0x00002063,        # branch with funct3=2
                     0x00001067]:       # jalr with funct3=1
            with self.subTest(hex(word)):
                sim, _, trace, _ = run(f".word {word}", max_instructions=1)
                self.assertEqual(sim.csr[rvsim.CSR_MCAUSE], rvsim.CAUSE_ILLEGAL)
                self.assertTrue(trace[0].startswith("T "))


class GeneratedProgramTests(unittest.TestCase):
    def test_pairs_is_up_to_date(self):
        """sw/tests/pairs.S is what sw/tests/gen_pairs.py produces."""
        sys.path.insert(0, os.path.join(ROOT, "sw", "tests"))
        try:
            import gen_pairs
        finally:
            sys.path.pop(0)
        with open(os.path.join(ROOT, "sw", "tests", "pairs.S"), encoding="ascii") as handle:
            self.assertEqual(handle.read(), gen_pairs.generate(),
                             "run python sw/tests/gen_pairs.py")

    def test_wpolicy_is_up_to_date(self):
        """sw/tests/wpolicy.S is what sw/tests/gen_wpolicy.py produces."""
        sys.path.insert(0, os.path.join(ROOT, "sw", "tests"))
        try:
            import gen_wpolicy
        finally:
            sys.path.pop(0)
        with open(os.path.join(ROOT, "sw", "tests", "wpolicy.S"), encoding="ascii") as handle:
            self.assertEqual(handle.read(), gen_wpolicy.generate(),
                             "run python sw/tests/gen_wpolicy.py")


class HierarchyProgramTests(unittest.TestCase):
    def test_hier_is_up_to_date(self):
        """sw/tests/hier.S is what sw/tests/gen_hier.py produces."""
        sys.path.insert(0, os.path.join(ROOT, "sw", "tests"))
        try:
            import gen_hier
        finally:
            sys.path.pop(0)
        with open(os.path.join(ROOT, "sw", "tests", "hier.S"), encoding="ascii") as handle:
            self.assertEqual(handle.read(), gen_hier.generate(),
                             "run python sw/tests/gen_hier.py")


class StaircaseTests(unittest.TestCase):
    def test_ws_walk_at_other_sizes(self):
        """The bench stage rewrites two lines of sw/bench/ws_walk.S to run it
        at other working-set sizes."""
        import regress
        programs = regress.staircase_programs()
        self.assertEqual(len(programs), 2 * len(regress.WS_SIZES))
        retired = {}
        for label, path, size, reads in programs:
            if size not in (256, 32768):
                continue
            with open(path, encoding="ascii") as handle:
                source = handle.read()
            self.assertIn(f".equ WS_BYTES, {size}\n", source)
            self.assertIn(f".equ READS, {reads}\n", source)
            sim, outcome, _, _ = run(source, max_instructions=200_000)
            self.assertEqual((outcome, sim.exit_code), ("halt", 0), label)
            retired[(size, reads)] = sim.retired
        small = retired[(256, 2 * regress.WS_READS)] - retired[(256, regress.WS_READS)]
        large = retired[(32768, 2 * regress.WS_READS)] - retired[(32768, regress.WS_READS)]
        self.assertLess(abs(small - large), 2 * regress.WS_READS // 8 + 16)


if __name__ == "__main__":
    unittest.main()
