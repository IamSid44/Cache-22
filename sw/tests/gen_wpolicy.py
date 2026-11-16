#!/usr/bin/env python3
"""Generates wpolicy.S: stores that have to survive the cache's write policy.

    python sw/tests/gen_wpolicy.py          # rewrites sw/tests/wpolicy.S
    python sw/tests/gen_wpolicy.py <file>   # writes it somewhere else
"""

import os
import sys

LINE = 32
SPAN = 0x1000               # distance between lines that share a set
AREA = 0x8000               # lines the checks store to: 0x8000..0x8fff
STREAM = 0xE000             # lines for the sequential checks: 0xe000..0xefff
SIZES = {1: ("sb", "lbu"), 2: ("sh", "lhu"), 4: ("sw", "lwu"), 8: ("sd", "ld")}


class Gen:
    def __init__(self):
        self.out = []
        self.mem = {}
        self.check = 0
        self.passes = 0
        self.loops = 0
        self.next_area = AREA
        self.next_stream = STREAM
        self.value = 0x0123456789ABCDEF

    def emit(self, text):
        self.out.append(f"    {text}")

    def begin(self, title):
        self.check += 1
        self.out.append("")
        self.out.append(f"    # check {self.check}: {title}")
        self.emit(f"li   gp, {self.check}")

    def lines(self, count=1, stream=False):
        """`count` fresh neighbouring lines; returns the first one's address."""
        if stream:
            base, self.next_stream = self.next_stream, self.next_stream + LINE * count
            assert self.next_stream <= STREAM + SPAN
        else:
            base, self.next_area = self.next_area, self.next_area + LINE * count
            assert self.next_area <= AREA + SPAN
        return base

    def fresh(self):
        """A new 64-bit value, different in every byte from the last one."""
        self.value = (self.value * 0x9E3779B97F4A7C15 + 0x1F) & (2**64 - 1)
        return self.value

    def store(self, addr, size, value=None):
        value = (self.fresh() if value is None else value) & (2 ** (8 * size) - 1)
        self.emit(f"li   t0, {addr:#x}")
        self.emit(f"li   t1, {value:#x}")
        self.emit(f"{SIZES[size][0]:<4} t1, 0(t0)")
        for index in range(size):
            self.mem[addr + index] = (value >> (8 * index)) & 0xFF

    def expect(self, addr, size=8):
        want = sum(self.mem.get(addr + index, 0) << (8 * index) for index in range(size))
        self.emit(f"li   t0, {addr:#x}")
        self.emit(f"{SIZES[size][1]:<4} t2, 0(t0)")
        self.emit(f"li   t3, {want:#x}")
        self.passes += 1                    # `fail` is too far for a branch
        self.emit(f"beq  t2, t3, ok_{self.passes}")
        self.emit("j    fail")
        self.out.append(f"ok_{self.passes}:")

    def store_loop(self, base, count, step, key):
        """A loop that stores (address xor key) at base, base + step, ..."""
        self.loops += 1
        self.emit(f"li   t0, {base:#x}")
        self.emit(f"li   t4, {base + step * count:#x}")
        self.emit(f"li   t1, {key:#x}")
        self.out.append(f"store_{self.loops}:")
        self.emit("xor  t2, t0, t1")
        self.emit("sd   t2, 0(t0)")
        self.emit(f"addi t0, t0, {step}")
        self.emit(f"bne  t0, t4, store_{self.loops}")
        for index in range(count):
            addr = base + step * index
            for byte in range(8):
                self.mem[addr + byte] = ((addr ^ key) >> (8 * byte)) & 0xFF

    def expect_sum(self, base, count, step):
        """A loop that adds up the doublewords at base, base + step, ..."""
        want = 0
        for index in range(count):
            addr = base + step * index
            want += sum(self.mem.get(addr + byte, 0) << (8 * byte) for byte in range(8))
        self.loops += 1
        self.emit(f"li   t0, {base:#x}")
        self.emit(f"li   t4, {base + step * count:#x}")
        self.emit("li   t5, 0")
        self.out.append(f"sum_{self.loops}:")
        self.emit("ld   t2, 0(t0)")
        self.emit("add  t5, t5, t2")
        self.emit(f"addi t0, t0, {step}")
        self.emit(f"bne  t0, t4, sum_{self.loops}")
        self.emit(f"li   t3, {want & (2**64 - 1):#x}")
        self.passes += 1
        self.emit(f"beq  t5, t3, ok_{self.passes}")
        self.emit("j    fail")
        self.out.append(f"ok_{self.passes}:")

    def evict(self, addr):
        """Push the line holding `addr` out of the cache."""
        line = addr - addr % LINE
        for step in range(1, 6):
            self.emit(f"li   t4, {line + SPAN * step:#x}")
            self.emit("ld   t5, 0(t4)")


def generate():
    g = Gen()

    # One store of each size, read back after its line has been evicted.
    for size, offset in ((8, 8), (4, 20), (2, 10), (1, 13)):
        a = g.lines()
        g.begin(f"a {size}-byte store, its line evicted, read back")
        g.store(a + offset, size)
        g.evict(a)
        g.expect(a + (offset & ~7))
        g.expect(a + offset, size)

    a = g.lines()
    g.begin("a store to an absent line, read back at once")
    g.store(a + 16, 8)
    g.expect(a + 16)
    g.expect(a + 8)                 # its neighbours are still zero
    g.expect(a + 24)

    a = g.lines()
    g.begin("four stores fill a line; read back at once and after eviction")
    for index in range(4):
        g.store(a + 8 * index, 8)
    for index in range(4):
        g.expect(a + 8 * index)
    g.evict(a)
    for index in range(4):
        g.expect(a + 8 * index)

    a = g.lines()
    g.begin("stores of different sizes over the same bytes: the latest wins")
    g.store(a + 8, 8)
    g.store(a + 11, 1)
    g.store(a + 12, 2)
    g.store(a + 8, 4)
    g.store(a + 14, 1)
    g.expect(a + 8)
    g.evict(a)
    g.expect(a + 8)
    g.expect(a + 12, 4)

    a = g.lines()
    g.begin("eight byte stores make one doubleword")
    for index in range(8):
        g.store(a + 24 + index, 1)
    g.evict(a)
    g.expect(a + 24)
    g.expect(a + 16)

    a = g.lines()
    b = a + SPAN
    g.begin("stores alternating between two lines that share a set")
    for _ in range(4):
        g.store(a, 8)
        g.store(b, 8)
    g.expect(a)
    g.expect(b)
    for _ in range(3):
        g.store(a + 8, 8)
        g.expect(b)
        g.store(b + 8, 8)
        g.expect(a + 8)
    g.expect(b + 8)
    g.expect(a)

    a = g.lines()
    g.begin("a store, an eviction, a store to another part of the absent line")
    g.store(a, 8)
    g.evict(a)
    g.store(a + 8, 8)
    g.evict(a)
    g.expect(a)
    g.expect(a + 8)
    g.expect(a + 16)

    a = g.lines()
    g.begin("the same doubleword stored again after its line was evicted")
    g.store(a + 16, 8)
    g.evict(a)
    g.store(a + 16, 8)
    g.expect(a + 16)
    g.evict(a)
    g.expect(a + 16)

    a = g.lines()
    b = a + 2 * SPAN
    g.begin("small stores to two lines in turn")
    for offset, size in ((1, 1), (2, 2), (4, 4), (9, 1), (10, 2), (12, 4)):
        g.store(a + offset, size)
        g.store(b + offset, size)
    g.expect(a)
    g.expect(b + 8)
    g.evict(a)
    g.expect(a + 8)
    g.expect(b)

    a = g.lines()
    g.begin("a line read first, then stored to, evicted and read again")
    g.expect(a + 8)
    g.store(a + 8, 8)
    g.store(a + 24, 4)
    g.evict(a)
    g.expect(a + 8)
    g.expect(a + 24)

    a = g.lines(6, stream=True)
    g.begin("sixteen stores in sequence over four lines, then read back")
    for index in range(16):
        g.store(a + 8 * index, 8)
    for index in range(16):
        g.expect(a + 8 * index)
    g.expect(a + 8 * 16)            # the line after them was not written

    a = g.lines(10, stream=True)
    g.begin("one store per line over eight lines, read back in reverse")
    for index in range(8):
        g.store(a + LINE * index + 8, 8)
    for index in reversed(range(8)):
        g.expect(a + LINE * index + 8)
        g.expect(a + LINE * index)

    a = g.lines(6, stream=True)
    g.begin("sequential byte stores over three lines, read back as doublewords")
    for index in range(0, 3 * LINE, 3):
        g.store(a + index, 1)
    for index in range(0, 3 * LINE, 8):
        g.expect(a + index)

    a = g.lines(26, stream=True)
    g.begin("a loop storing one doubleword per line over 24 lines")
    g.store_loop(a, 24, LINE, 0x5A5A_0000_1234_0000)
    g.expect_sum(a, 24, LINE)
    g.expect_sum(a + 8, 24, LINE)       # the rest of each line is still zero

    a = g.lines(10, stream=True)
    g.begin("a loop storing every doubleword of eight lines, twice")
    g.store_loop(a, 32, 8, 0x0F0F_1111_0000_2222)
    g.store_loop(a, 32, 8, 0x3333_0000_4444_00F0)
    g.expect_sum(a, 32, 8)
    g.expect_sum(a, 8, LINE)

    header = [
        "# wpolicy.S - stores that have to survive the cache's write policy.",
        "# GENERATED by sw/tests/gen_wpolicy.py; do not edit by hand.",
        f"# Checks: {g.check}. Expected values are worked out by the generator.",
        ".equ HALT, 0x10000008",
    ]
    footer = [
        "",
        "pass:",
        "    li   t6, HALT",
        "    sd   x0, 0(t6)",
        "pass_loop:",
        "    j    pass_loop",
        "fail:",
        "    li   t6, HALT",
        "    sd   gp, 0(t6)",
        "fail_loop:",
        "    j    fail_loop",
        "",
    ]
    return "\n".join(header + g.out + footer)


if __name__ == "__main__":
    path = (sys.argv[1] if len(sys.argv) > 1 else
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "wpolicy.S"))
    with open(path, "w", encoding="ascii", newline="\n") as handle:
        handle.write(generate())
    print(f"wrote {path}")
