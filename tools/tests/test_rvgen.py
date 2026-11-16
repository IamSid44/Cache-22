"""Tests for the random program generator (tools/rvgen.py)."""

import io
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import rvasm  # noqa: E402
import rvgen  # noqa: E402
import rvsim  # noqa: E402

SEEDS = range(150)
MAX_RUN = 200_000

EXPECTED_MNEMONICS = set(
    "add sub sll slt sltu xor srl sra or and "
    "addi slti sltiu xori ori andi slli srli srai "
    "addw subw sllw srlw sraw addiw slliw srliw sraiw lui auipc "
    "lb lh lw ld lbu lhu lwu sb sh sw sd "
    "beq bne blt bge bltu bgeu jal jalr".split())

_BRANCH = {0: "beq", 1: "bne", 4: "blt", 5: "bge", 6: "bltu", 7: "bgeu"}
_LOAD = {0: "lb", 1: "lh", 2: "lw", 3: "ld", 4: "lbu", 5: "lhu", 6: "lwu"}
_STORE = {0: "sb", 1: "sh", 2: "sw", 3: "sd"}
_OPIMM = {0: "addi", 2: "slti", 3: "sltiu", 4: "xori", 6: "ori", 7: "andi"}
_OP = {0: ("add", "sub"), 1: ("sll", None), 2: ("slt", None), 3: ("sltu", None),
       4: ("xor", None), 5: ("srl", "sra"), 6: ("or", None), 7: ("and", None)}
_OP32 = {0: ("addw", "subw"), 1: ("sllw", None), 5: ("srlw", "sraw")}
_OPIMM32 = {0: ("addiw", None), 1: ("slliw", None), 5: ("srliw", "sraiw")}


def decode(word):
    """Mnemonic of a 32-bit RV64I instruction word (independent of rvasm)."""
    opc, f3, bit30 = word & 0x7F, (word >> 12) & 7, (word >> 30) & 1
    if opc == 0x37:
        return "lui"
    if opc == 0x17:
        return "auipc"
    if opc == 0x6F:
        return "jal"
    if opc == 0x67:
        return "jalr"
    if opc == 0x63:
        return _BRANCH[f3]
    if opc == 0x03:
        return _LOAD[f3]
    if opc == 0x23:
        return _STORE[f3]
    if opc == 0x13:
        if f3 == 1:
            return "slli"
        if f3 == 5:
            return "srai" if bit30 else "srli"
        return _OPIMM[f3]
    if opc == 0x33:
        return _OP[f3][bit30]
    if opc == 0x3B:
        return _OP32[f3][bit30]
    if opc == 0x1B:
        return _OPIMM32[f3][bit30 if f3 == 5 else 0]
    return f"?{word:08x}"


_STORE_RE = re.compile(r"m\[([0-9a-f]{16}),(\d)\]")


def run_seed(seed, length=300):
    source = rvgen.generate(seed, length)
    image, _, symbols = rvasm.assemble(source)
    trace = io.StringIO()
    sim = rvsim.Simulator(image, trace=trace)
    outcome = sim.run(MAX_RUN)
    lines = trace.getvalue().splitlines()
    info = {"seed": seed, "outcome": outcome, "exit": sim.exit_code,
            "traps": sim.trap_count, "instret": sim.retired,
            "mnemonics": set(), "taken": set(), "not_taken": set(),
            "bad_stores": [], "last": lines[-1],
            "image_len": len(image)}
    pcs = [int(line.split()[1], 16) for line in lines]
    scratch = symbols["scratch"]
    for i, line in enumerate(lines):
        parts = line.split()
        if parts[0] != "C":
            continue
        word = int(parts[2], 16)
        mnem = decode(word)
        info["mnemonics"].add(mnem)
        if mnem in _BRANCH.values() and i + 1 < len(lines):
            (info["not_taken"] if pcs[i + 1] == pcs[i] + 4 else info["taken"]).add(mnem)
        for m in _STORE_RE.finditer(line):
            addr = int(m.group(1), 16)
            if addr == rvsim.MMIO_HALT:
                continue
            if not (scratch <= addr and addr + int(m.group(2)) <= scratch + 512):
                info["bad_stores"].append(addr)
    # the code (everything below the scratch area) must never change
    info["code_intact"] = bytes(sim.mem[:scratch]) == image[:scratch]
    return info


class RvgenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = [run_seed(seed) for seed in SEEDS]

    def test_every_seed_halts_cleanly(self):
        for info in self.runs:
            with self.subTest(seed=info["seed"]):
                self.assertEqual(info["outcome"], "halt")
                self.assertEqual(info["exit"], 0)
                self.assertEqual(info["traps"], 0)
                self.assertIn("m[0000000010000008,8]=0000000000000000", info["last"])
                self.assertLess(info["image_len"], rvsim.RAM_SIZE)

    def test_stores_stay_in_scratch_and_code_is_intact(self):
        for info in self.runs:
            with self.subTest(seed=info["seed"]):
                self.assertEqual(info["bad_stores"], [])
                self.assertTrue(info["code_intact"])

    def test_deterministic(self):
        for seed in (0, 1, 7, 99):
            self.assertEqual(rvgen.generate(seed), rvgen.generate(seed))
            self.assertEqual(rvgen.generate(seed, 120), rvgen.generate(seed, 120))
        texts = {rvgen.generate(seed) for seed in range(20)}
        self.assertEqual(len(texts), 20)
        self.assertNotEqual(rvgen.generate(3, 100), rvgen.generate(3, 200))

    def test_other_lengths_are_valid(self):
        for seed, length in [(1, 1), (2, 20), (3, 1000), (4, 3000)]:
            with self.subTest(seed=seed, length=length):
                info = run_seed(seed, length)
                self.assertEqual((info["outcome"], info["exit"], info["traps"]),
                                 ("halt", 0, 0))

    def test_mnemonic_coverage(self):
        seen = set().union(*(i["mnemonics"] for i in self.runs))
        self.assertFalse(any(m.startswith("?") for m in seen), seen)
        self.assertEqual(EXPECTED_MNEMONICS - seen, set(), "never executed")
        # nothing outside the supported set: no system/fence instructions
        self.assertEqual(seen - EXPECTED_MNEMONICS, set())

    def test_branch_outcome_coverage(self):
        taken = set().union(*(i["taken"] for i in self.runs))
        not_taken = set().union(*(i["not_taken"] for i in self.runs))
        branches = set(_BRANCH.values())
        self.assertEqual(branches - taken, set(), "never taken")
        self.assertEqual(branches - not_taken, set(), "never not-taken")

    def test_dynamic_length_report(self):
        counts = [i["instret"] for i in self.runs]
        avg = sum(counts) / len(counts)
        print(f"\nrvgen dynamic instructions over {len(counts)} seeds (length 300): "
              f"avg {avg:.0f}, min {min(counts)}, max {max(counts)}")
        self.assertGreater(avg, 500)
        self.assertLess(max(counts), 20_000)

    def test_plain_mode_is_frozen(self):
        """Adding features must not change what an existing seed generates,
        or old failing seeds could no longer be reproduced."""
        import hashlib
        expected = {0: "975e355b9c8385a4", 1: "6505afb230018e49",
                    7: "696e047acde3cf84", 99: "0a7e08d4d532b5c6"}
        for seed, digest in expected.items():
            text = rvgen.generate(seed)
            self.assertEqual(hashlib.sha256(text.encode()).hexdigest()[:16], digest)
            self.assertEqual(text, rvgen.generate(seed, traps=False))


def run_trap_seed(seed):
    """Run one trap-mode program; collect causes, CSR forms and store safety."""
    image, _, symbols = rvasm.assemble(rvgen.generate(seed, traps=True))
    trace = io.StringIO()
    sim = rvsim.Simulator(image, trace=trace)
    outcome = sim.run(MAX_RUN)
    scratch = symbols["scratch"]
    info = {"seed": seed, "outcome": outcome, "exit": sim.exit_code,
            "traps": sim.trap_count, "retired": 0, "causes": set(),
            "csr_forms": set(), "csr_addrs": set(), "bad_stores": [],
            "code_intact": bytes(sim.mem[:scratch]) == image[:scratch]}
    for line in trace.getvalue().splitlines():
        parts = line.split()
        if parts[0] == "T":
            info["causes"].add(int(parts[3].split("=")[1]))
            continue
        info["retired"] += 1
        word = int(parts[2], 16)
        funct3 = (word >> 12) & 7
        if word & 0x7F == 0x73 and funct3 not in (0, 4):
            info["csr_forms"].add(funct3)
            info["csr_addrs"].add(word >> 20)
        for m in _STORE_RE.finditer(line):
            addr = int(m.group(1), 16)
            if addr != rvsim.MMIO_HALT and not (
                    scratch <= addr and addr + int(m.group(2)) <= scratch + 512):
                info["bad_stores"].append(addr)
    return info


class RvgenTrapModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = [run_trap_seed(seed) for seed in SEEDS]

    def test_every_seed_halts_cleanly(self):
        for info in self.runs:
            with self.subTest(seed=info["seed"]):
                self.assertEqual((info["outcome"], info["exit"]), ("halt", 0))
                self.assertEqual(info["bad_stores"], [])
                self.assertTrue(info["code_intact"])

    def test_every_cause_occurs(self):
        seen = set().union(*(i["causes"] for i in self.runs))
        # Cause 1 (fetch outside RAM) cannot be generated safely at random;
        # sw/tests/precise.S covers it.
        self.assertEqual(seen, {0, 2, 3, 4, 5, 6, 7, 11})

    def test_every_csr_form_is_executed(self):
        seen = set().union(*(i["csr_forms"] for i in self.runs))
        self.assertEqual(seen, {1, 2, 3, 5, 6, 7})

    def test_mcycle_is_never_accessed(self):
        """mcycle is timing-dependent: the RTL and the model differ on it."""
        seen = set().union(*(i["csr_addrs"] for i in self.runs))
        self.assertNotIn(0xB00, seen)

    def test_trap_mode_report(self):
        retired = sum(i["retired"] for i in self.runs) / len(self.runs)
        traps = sum(i["traps"] for i in self.runs) / len(self.runs)
        print(f"\nrvgen trap mode over {len(self.runs)} seeds: "
              f"avg {retired:.0f} instructions and {traps:.0f} traps per program")
        self.assertGreater(traps, 10)
        self.assertLess(max(i["retired"] for i in self.runs), 50_000)


if __name__ == "__main__":
    unittest.main()
