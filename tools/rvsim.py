#!/usr/bin/env python3
"""rvsim - golden-model instruction-set simulator for RV64I + M-mode traps.

    python tools/rvsim.py prog.S            # assemble and run
    python tools/rvsim.py prog.hex --trace out.trace
"""

import argparse
import struct
import sys

import rvasm
from rvasm import sext

MASK64 = (1 << 64) - 1

RAM_SIZE = 1 << 16              # bytes of RAM starting at address 0
RESET_PC = 0
MMIO_CONSOLE = 0x10000000       # store: low byte is printed
MMIO_HALT = 0x10000008          # store: stop simulation, value = exit code

# ---- Exception causes -----------------------------------------------------
CAUSE_INSTR_MISALIGNED = 0
CAUSE_INSTR_ACCESS = 1
CAUSE_ILLEGAL = 2
CAUSE_BREAKPOINT = 3
CAUSE_LOAD_MISALIGNED = 4
CAUSE_LOAD_ACCESS = 5
CAUSE_STORE_MISALIGNED = 6
CAUSE_STORE_ACCESS = 7
CAUSE_ECALL_M = 11

# ---- CSRs -----------------------------------------------------------------
CSR_MSTATUS, CSR_MISA, CSR_MIE, CSR_MTVEC = 0x300, 0x301, 0x304, 0x305
CSR_MSCRATCH, CSR_MEPC, CSR_MCAUSE, CSR_MTVAL, CSR_MIP = (
    0x340, 0x341, 0x342, 0x343, 0x344)
CSR_MCYCLE, CSR_MINSTRET = 0xB00, 0xB02
CSR_MVENDORID, CSR_MARCHID, CSR_MIMPID, CSR_MHARTID = 0xF11, 0xF12, 0xF13, 0xF14

MSTATUS_MIE = 1 << 3
MSTATUS_MPIE = 1 << 7
MSTATUS_MPP = 3 << 11           # hard-wired to 3: machine mode only
MISA_VALUE = (2 << 62) | (1 << 8)   # MXL=64, extension I

READ_ONLY_ZERO = {CSR_MVENDORID, CSR_MARCHID, CSR_MIMPID, CSR_MHARTID}
WRITABLE = {CSR_MSTATUS, CSR_MISA, CSR_MIE, CSR_MTVEC, CSR_MSCRATCH,
            CSR_MEPC, CSR_MCAUSE, CSR_MTVAL, CSR_MIP,
            CSR_MCYCLE, CSR_MINSTRET}


def memory_hash(mem):
    value = 0xCBF29CE484222325
    for (word,) in struct.iter_unpack("<Q", bytes(mem)):
        value = ((value ^ word) * 0x100000001B3) & MASK64
    return value


class Trap(Exception):
    def __init__(self, cause, tval=0):
        self.cause, self.tval = cause, tval & MASK64


class Simulator:
    """RV64I machine-mode simulator."""

    def __init__(self, image=b"", ram_size=RAM_SIZE, trace=None):
        if len(image) > ram_size:
            raise ValueError("program image is larger than RAM")
        self.mem = bytearray(ram_size)
        self.mem[:len(image)] = image
        self.x = [0] * 32
        self.pc = RESET_PC
        self.csr = {CSR_MSTATUS: MSTATUS_MPP, CSR_MIE: 0, CSR_MTVEC: 0,
                    CSR_MSCRATCH: 0, CSR_MEPC: 0, CSR_MCAUSE: 0,
                    CSR_MTVAL: 0, CSR_MIP: 0}
        self.instret = 0                    # the minstret CSR; programs can write it
        self.retired = 0                    # instructions actually retired (statistic)
        self.halted = False
        self.exit_code = None
        self.console = bytearray()
        self.trace = trace                  # file-like or None
        self.trap_count = 0

    # -- memory -------------------------------------------------------------
    def _check(self, addr, size, misaligned_cause, access_cause):
        if addr % size:
            raise Trap(misaligned_cause, addr)
        if addr + size > len(self.mem):
            raise Trap(access_cause, addr)

    def load(self, addr, size):
        self._check(addr, size, CAUSE_LOAD_MISALIGNED, CAUSE_LOAD_ACCESS)
        return int.from_bytes(self.mem[addr:addr + size], "little")

    def store(self, addr, size, value):
        """Returns False if the store went to MMIO instead of RAM."""
        if addr % size:
            raise Trap(CAUSE_STORE_MISALIGNED, addr)
        if addr == MMIO_CONSOLE:
            self.console.append(value & 0xFF)
            return False
        if addr == MMIO_HALT:
            self.halted = True
            self.exit_code = value & MASK64
            return False
        if addr + size > len(self.mem):
            raise Trap(CAUSE_STORE_ACCESS, addr)
        self.mem[addr:addr + size] = (value & ((1 << (8 * size)) - 1)).to_bytes(size, "little")
        return True

    # -- CSRs ---------------------------------------------------------------
    def csr_read(self, addr):
        if addr == CSR_MISA:
            return MISA_VALUE
        if addr in READ_ONLY_ZERO:
            return 0
        if addr in (CSR_MCYCLE, CSR_MINSTRET):
            # The model has no notion of time: mcycle reads as minstret.
            return self.instret & MASK64
        if addr in self.csr:
            return self.csr[addr]
        raise Trap(CAUSE_ILLEGAL)

    def csr_write(self, addr, value):
        value &= MASK64
        if addr == CSR_MSTATUS:
            self.csr[addr] = (value & (MSTATUS_MIE | MSTATUS_MPIE)) | MSTATUS_MPP
        elif addr == CSR_MTVEC:
            self.csr[addr] = value & ~3         # direct mode only
        elif addr == CSR_MEPC:
            self.csr[addr] = value & ~3         # IALIGN = 32
        elif addr == CSR_MINSTRET:
            self.instret = value
        elif addr in (CSR_MISA, CSR_MCYCLE):
            pass                                # writes ignored
        elif addr in self.csr:
            self.csr[addr] = value

    # -- execution ----------------------------------------------------------
    def step(self):
        """Execute one instruction (or take one trap)."""
        pc = self.pc
        instr = 0
        record = []                             # state changes for the trace
        try:
            if pc % 4:
                raise Trap(CAUSE_INSTR_MISALIGNED, pc)
            if pc + 4 > len(self.mem):
                raise Trap(CAUSE_INSTR_ACCESS, pc)
            instr = int.from_bytes(self.mem[pc:pc + 4], "little")
            next_pc = self._execute(pc, instr, record)
        except Trap as trap:
            self._take_trap(pc, instr, trap)
            return
        self.pc = next_pc & MASK64
        self.instret += 1
        self.retired += 1
        if self.trace is not None:
            self.trace.write(" ".join([f"C {pc:016x} {instr:08x}"] + record) + "\n")

    def _take_trap(self, pc, instr, trap):
        status = self.csr[CSR_MSTATUS]
        mpie = MSTATUS_MPIE if status & MSTATUS_MIE else 0
        self.csr[CSR_MSTATUS] = MSTATUS_MPP | mpie      # MIE cleared
        self.csr[CSR_MEPC] = pc
        self.csr[CSR_MCAUSE] = trap.cause
        self.csr[CSR_MTVAL] = trap.tval
        self.pc = self.csr[CSR_MTVEC]
        self.trap_count += 1
        if self.trace is not None:
            self.trace.write(f"T {pc:016x} {instr:08x} cause={trap.cause} "
                             f"tval={trap.tval:016x}\n")

    def _set(self, rd, value, record):
        if rd:
            value &= MASK64
            self.x[rd] = value
            record.append(f"x{rd}={value:016x}")

    def _jump(self, target):
        target &= MASK64
        if target % 4:
            raise Trap(CAUSE_INSTR_MISALIGNED, target)
        return target

    def _execute(self, pc, instr, record):
        opcode = instr & 0x7F
        rd = (instr >> 7) & 0x1F
        f3 = (instr >> 12) & 7
        rs1 = (instr >> 15) & 0x1F
        rs2 = (instr >> 20) & 0x1F
        f7 = instr >> 25
        a, b = self.x[rs1], self.x[rs2]
        imm_i = sext(instr >> 20, 12)
        illegal = Trap(CAUSE_ILLEGAL, instr)

        if opcode == 0x37:                              # lui
            self._set(rd, sext(instr & 0xFFFFF000, 32), record)
        elif opcode == 0x17:                            # auipc
            self._set(rd, pc + sext(instr & 0xFFFFF000, 32), record)
        elif opcode == 0x6F:                            # jal
            off = sext(((instr >> 31) << 20) | (((instr >> 12) & 0xFF) << 12) |
                       (((instr >> 20) & 1) << 11) | (((instr >> 21) & 0x3FF) << 1), 21)
            target = self._jump(pc + off)
            self._set(rd, pc + 4, record)
            return target
        elif opcode == 0x67:                            # jalr
            if f3:
                raise illegal
            target = self._jump((a + imm_i) & ~1)
            self._set(rd, pc + 4, record)
            return target
        elif opcode == 0x63:                            # branches
            off = sext(((instr >> 31) << 12) | (((instr >> 7) & 1) << 11) |
                       (((instr >> 25) & 0x3F) << 5) | (((instr >> 8) & 0xF) << 1), 13)
            sa, sb = sext(a, 64), sext(b, 64)
            if f3 == 0:
                taken = a == b
            elif f3 == 1:
                taken = a != b
            elif f3 == 4:
                taken = sa < sb
            elif f3 == 5:
                taken = sa >= sb
            elif f3 == 6:
                taken = a < b
            elif f3 == 7:
                taken = a >= b
            else:
                raise illegal
            if taken:
                return self._jump(pc + off)
        elif opcode == 0x03:                            # loads
            if f3 == 7:
                raise illegal
            size = 1 << (f3 & 3)
            value = self.load((a + imm_i) & MASK64, size)
            if f3 < 4:                                  # lb/lh/lw/ld sign-extend
                value = sext(value, 8 * size)
            self._set(rd, value, record)
        elif opcode == 0x23:                            # stores
            if f3 > 3:
                raise illegal
            size = 1 << f3
            addr = (a + sext(((instr >> 25) << 5) | rd, 12)) & MASK64
            value = b & ((1 << (8 * size)) - 1)
            self.store(addr, size, value)
            record.append(f"m[{addr:016x},{size}]={value:016x}")
        elif opcode == 0x13:                            # op-imm
            self._set(rd, self._alu_imm(a, instr, f3, imm_i, 64, illegal), record)
        elif opcode == 0x1B:                            # op-imm-32
            if f3 not in (0, 1, 5):
                raise illegal
            self._set(rd, self._alu_imm(a, instr, f3, imm_i, 32, illegal), record)
        elif opcode == 0x33:                            # op
            self._set(rd, self._alu_reg(a, b, f3, f7, 64, illegal), record)
        elif opcode == 0x3B:                            # op-32
            if f3 not in (0, 1, 5):
                raise illegal
            self._set(rd, self._alu_reg(a, b, f3, f7, 32, illegal), record)
        elif opcode == 0x0F:                            # fence: no-op
            pass
        elif opcode == 0x73:                            # system
            return self._system(pc, instr, rd, f3, rs1, record, illegal)
        else:
            raise illegal
        return pc + 4

    @staticmethod
    def _alu_imm(a, instr, f3, imm, width, illegal):
        shamt_bits = 6 if width == 64 else 5
        shamt = (instr >> 20) & ((1 << shamt_bits) - 1)
        upper = instr >> (20 + shamt_bits)              # must be 0 or 0b0100..0
        arith = 1 << (10 - shamt_bits)
        operand = a & ((1 << width) - 1)
        if f3 == 0:
            result = operand + imm
        elif f3 == 1:
            if upper:
                raise illegal
            result = operand << shamt
        elif f3 == 5:
            if upper == 0:
                result = operand >> shamt
            elif upper == arith:
                result = sext(operand, width) >> shamt
            else:
                raise illegal
        elif f3 == 2:
            result = int(sext(a, 64) < imm)
        elif f3 == 3:
            result = int(a < (imm & MASK64))
        elif f3 == 4:
            result = a ^ imm
        elif f3 == 6:
            result = a | imm
        else:
            result = a & imm
        return sext(result, 32) if width == 32 else result

    @staticmethod
    def _alu_reg(a, b, f3, f7, width, illegal):
        if f7 not in (0x00, 0x20) or (f7 == 0x20 and f3 not in (0, 5)):
            raise illegal
        mask = (1 << width) - 1
        shamt = b & (width - 1)
        ua, ub = a & mask, b & mask
        if f3 == 0:
            result = ua - ub if f7 else ua + ub
        elif f3 == 1:
            result = ua << shamt
        elif f3 == 2:
            result = int(sext(a, 64) < sext(b, 64))
        elif f3 == 3:
            result = int(a < b)
        elif f3 == 4:
            result = a ^ b
        elif f3 == 5:
            result = (sext(ua, width) >> shamt) if f7 else (ua >> shamt)
        elif f3 == 6:
            result = a | b
        else:
            result = a & b
        return sext(result, 32) if width == 32 else result

    def _system(self, pc, instr, rd, f3, rs1, record, illegal):
        if f3 == 0:
            if instr == 0x00000073:
                raise Trap(CAUSE_ECALL_M, 0)
            if instr == 0x00100073:
                raise Trap(CAUSE_BREAKPOINT, pc)
            if instr == 0x30200073:                     # mret
                status = self.csr[CSR_MSTATUS]
                mie = MSTATUS_MIE if status & MSTATUS_MPIE else 0
                self.csr[CSR_MSTATUS] = MSTATUS_MPP | MSTATUS_MPIE | mie
                return self.csr[CSR_MEPC]
            if instr == 0x10500073:                     # wfi: no-op
                return pc + 4
            raise illegal
        if f3 == 4:
            raise illegal
        addr = instr >> 20
        source = rs1 if f3 >= 5 else self.x[rs1]        # zimm or register
        kind = f3 & 3                                   # 1 write, 2 set, 3 clear
        writes = kind == 1 or rs1 != 0
        if writes and (addr >> 10) == 3:                # read-only CSR space
            raise illegal
        try:
            old = self.csr_read(addr)
        except Trap:
            raise illegal from None
        if writes:
            if addr not in WRITABLE:
                raise illegal
            new = source if kind == 1 else (old | source if kind == 2 else old & ~source)
            self.csr_write(addr, new)
        self._set(rd, old, record)
        return pc + 4

    def run(self, max_instructions=1_000_000):
        """Run until the program halts."""
        steps = 0
        while not self.halted:
            if steps >= max_instructions:
                return "timeout"
            self.step()
            steps += 1
        return "halt"


def load_program(path):
    """Load a .S/.s (assembled on the fly) or a byte-per-line .hex file."""
    if path.lower().endswith((".s", ".asm")):
        image, _, _ = rvasm.assemble_file(path)
        return image
    with open(path, encoding="ascii") as handle:
        return bytes(int(tok, 16) for tok in handle.read().split())


def main(argv=None):
    parser = argparse.ArgumentParser(description="RV64I golden-model simulator")
    parser.add_argument("program", help=".S source or .hex image")
    parser.add_argument("--trace", help="write the commit trace to this file")
    parser.add_argument("--max", type=int, default=1_000_000,
                        help="instruction limit (default 1,000,000)")
    parser.add_argument("--regs", action="store_true", help="print final registers")
    args = parser.parse_args(argv)
    try:
        image = load_program(args.program)
    except rvasm.AsmError as err:
        print(f"{args.program}: {err}", file=sys.stderr)
        return 2
    trace = open(args.trace, "w", encoding="ascii") if args.trace else None
    sim = Simulator(image, trace=trace)
    outcome = sim.run(args.max)
    if trace:
        trace.close()
    if sim.console:
        sys.stdout.write(sim.console.decode("latin-1"))
    if args.regs:
        for i in range(32):
            print(f"x{i:<2} {sim.x[i]:016x}")
    if outcome == "timeout":
        print(f"TIMEOUT after {args.max} instructions (pc={sim.pc:#x})")
        return 3
    status = "PASS" if sim.exit_code == 0 else f"FAIL (exit code {sim.exit_code})"
    print(f"{status}: {sim.retired} instructions, {sim.trap_count} traps")
    return 0 if sim.exit_code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
