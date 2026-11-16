#!/usr/bin/env python3
"""rvasm - a small two-pass RV64I + Zicsr assembler.

    python tools/rvasm.py prog.S -o prog.hex [--lst prog.lst]
"""

import argparse
import ast
import re
import sys

XLEN = 64
MASK64 = (1 << 64) - 1


class AsmError(Exception):
    """Assembly error carrying the source line number."""

    def __init__(self, msg, lineno=None, line=None):
        self.msg, self.lineno, self.line = msg, lineno, line
        where = f"line {lineno}: " if lineno else ""
        src = f"\n    {line.strip()}" if line else ""
        super().__init__(f"{where}{msg}{src}")


# --------------------------------------------------------------------------
# Registers and CSRs
# --------------------------------------------------------------------------
_ABI = ("zero ra sp gp tp t0 t1 t2 s0 s1 a0 a1 a2 a3 a4 a5 a6 a7 "
        "s2 s3 s4 s5 s6 s7 s8 s9 s10 s11 t3 t4 t5 t6").split()
REGS = {f"x{i}": i for i in range(32)}
REGS.update({name: i for i, name in enumerate(_ABI)})
REGS["fp"] = 8

CSRS = {
    "mstatus": 0x300, "misa": 0x301, "mie": 0x304, "mtvec": 0x305,
    "mscratch": 0x340, "mepc": 0x341, "mcause": 0x342, "mtval": 0x343,
    "mip": 0x344, "mcycle": 0xB00, "minstret": 0xB02,
    "mvendorid": 0xF11, "marchid": 0xF12, "mimpid": 0xF13, "mhartid": 0xF14,
}

# --------------------------------------------------------------------------
# Instruction tables: mnemonic -> (opcode, funct3, funct7)
# --------------------------------------------------------------------------
OP, OP32, OPIMM, OPIMM32 = 0x33, 0x3B, 0x13, 0x1B
LOAD, STORE, BRANCH, JAL, JALR, LUI, AUIPC, SYSTEM = (
    0x03, 0x23, 0x63, 0x6F, 0x67, 0x37, 0x17, 0x73)

R_TYPE = {
    "add": (OP, 0, 0x00), "sub": (OP, 0, 0x20), "sll": (OP, 1, 0x00),
    "slt": (OP, 2, 0x00), "sltu": (OP, 3, 0x00), "xor": (OP, 4, 0x00),
    "srl": (OP, 5, 0x00), "sra": (OP, 5, 0x20), "or": (OP, 6, 0x00),
    "and": (OP, 7, 0x00),
    "addw": (OP32, 0, 0x00), "subw": (OP32, 0, 0x20), "sllw": (OP32, 1, 0x00),
    "srlw": (OP32, 5, 0x00), "sraw": (OP32, 5, 0x20),
}
I_ARITH = {
    "addi": (OPIMM, 0), "slti": (OPIMM, 2), "sltiu": (OPIMM, 3),
    "xori": (OPIMM, 4), "ori": (OPIMM, 6), "andi": (OPIMM, 7),
    "addiw": (OPIMM32, 0),
}
# mnemonic -> (opcode, funct3, upper immediate bits, shamt width)
SHIFT_IMM = {
    "slli": (OPIMM, 1, 0x000, 6), "srli": (OPIMM, 5, 0x000, 6),
    "srai": (OPIMM, 5, 0x400, 6),
    "slliw": (OPIMM32, 1, 0x000, 5), "srliw": (OPIMM32, 5, 0x000, 5),
    "sraiw": (OPIMM32, 5, 0x400, 5),
}
LOADS = {"lb": 0, "lh": 1, "lw": 2, "ld": 3, "lbu": 4, "lhu": 5, "lwu": 6}
STORES = {"sb": 0, "sh": 1, "sw": 2, "sd": 3}
BRANCHES = {"beq": 0, "bne": 1, "blt": 4, "bge": 5, "bltu": 6, "bgeu": 7}
CSR_OPS = {"csrrw": 1, "csrrs": 2, "csrrc": 3,
           "csrrwi": 5, "csrrsi": 6, "csrrci": 7}
FIXED = {"ecall": 0x00000073, "ebreak": 0x00100073, "mret": 0x30200073,
         "wfi": 0x10500073, "fence": 0x0FF0000F}

# Pseudo-instructions that are a pure textual rewrite: name -> (arity, template)
REWRITES = {
    "nop": (0, "addi x0, x0, 0"),
    "mv": (2, "addi {0}, {1}, 0"),
    "not": (2, "xori {0}, {1}, -1"),
    "neg": (2, "sub {0}, x0, {1}"),
    "negw": (2, "subw {0}, x0, {1}"),
    "sext.w": (2, "addiw {0}, {1}, 0"),
    "seqz": (2, "sltiu {0}, {1}, 1"),
    "snez": (2, "sltu {0}, x0, {1}"),
    "sltz": (2, "slt {0}, {1}, x0"),
    "sgtz": (2, "slt {0}, x0, {1}"),
    "beqz": (2, "beq {0}, x0, {1}"),
    "bnez": (2, "bne {0}, x0, {1}"),
    "blez": (2, "bge x0, {0}, {1}"),
    "bgez": (2, "bge {0}, x0, {1}"),
    "bltz": (2, "blt {0}, x0, {1}"),
    "bgtz": (2, "blt x0, {0}, {1}"),
    "bgt": (3, "blt {1}, {0}, {2}"),
    "ble": (3, "bge {1}, {0}, {2}"),
    "bgtu": (3, "bltu {1}, {0}, {2}"),
    "bleu": (3, "bgeu {1}, {0}, {2}"),
    "j": (1, "jal x0, {0}"),
    "jr": (1, "jalr x0, 0({0})"),
    "ret": (0, "jalr x0, 0(x1)"),
    "call": (1, "jal x1, {0}"),
    "csrr": (2, "csrrs {0}, {1}, x0"),
    "csrw": (2, "csrrw x0, {0}, {1}"),
    "csrs": (2, "csrrs x0, {0}, {1}"),
    "csrc": (2, "csrrc x0, {0}, {1}"),
    "csrwi": (2, "csrrwi x0, {0}, {1}"),
    "csrsi": (2, "csrrsi x0, {0}, {1}"),
    "csrci": (2, "csrrci x0, {0}, {1}"),
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def sext(value, bits):
    """Interpret the low `bits` of value as a two's-complement number."""
    value &= (1 << bits) - 1
    return value - (1 << bits) if value >> (bits - 1) else value


def _fits_signed(value, bits):
    return -(1 << (bits - 1)) <= value < (1 << (bits - 1))


def enc_r(opc, f3, f7, rd, rs1, rs2):
    return (f7 << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | opc


def enc_i(opc, f3, rd, rs1, imm):
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | opc


def enc_s(opc, f3, rs1, rs2, imm):
    imm &= 0xFFF
    return ((imm >> 5) << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | \
           ((imm & 0x1F) << 7) | opc


def enc_b(opc, f3, rs1, rs2, off):
    off &= 0x1FFF
    return (((off >> 12) & 1) << 31) | (((off >> 5) & 0x3F) << 25) | \
           (rs2 << 20) | (rs1 << 15) | (f3 << 12) | \
           (((off >> 1) & 0xF) << 8) | (((off >> 11) & 1) << 7) | opc


def enc_u(opc, rd, imm20):
    return ((imm20 & 0xFFFFF) << 12) | (rd << 7) | opc


def enc_j(opc, rd, off):
    off &= 0x1FFFFF
    return (((off >> 20) & 1) << 31) | (((off >> 1) & 0x3FF) << 21) | \
           (((off >> 11) & 1) << 20) | (((off >> 12) & 0xFF) << 12) | \
           (rd << 7) | opc


def li_sequence(rd, value):
    """Instruction words that load the 64-bit constant `value` into rd."""
    value = sext(value, 64)
    if _fits_signed(value, 32):
        lo = sext(value, 12)
        hi = ((value - lo) >> 12) & 0xFFFFF
        if hi == 0:
            return [enc_i(OPIMM, 0, rd, 0, lo)]
        words = [enc_u(LUI, rd, hi)]
        if lo:
            words.append(enc_i(OPIMM32, 0, rd, rd, lo))     # addiw
        return words
    lo = sext(value, 12)
    rest = (value - lo) >> 12
    shift = 12
    while rest & 1 == 0:
        rest >>= 1
        shift += 1
    words = li_sequence(rd, rest)
    words.append(enc_i(OPIMM, 1, rd, rd, shift))            # slli
    if lo:
        words.append(enc_i(OPIMM, 0, rd, rd, lo))           # addi
    return words


_ALLOWED_BINOPS = {
    ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a // b,   # "/" is integer division
    ast.Mod: lambda a, b: a % b, ast.LShift: lambda a, b: a << b,
    ast.RShift: lambda a, b: a >> b, ast.BitAnd: lambda a, b: a & b,
    ast.BitOr: lambda a, b: a | b, ast.BitXor: lambda a, b: a ^ b,
}


_TOKEN_RE = re.compile(r"0[xX][0-9a-fA-F]+|0[bB][01]+|\d+|[A-Za-z_.][A-Za-z0-9_.$]*")


def eval_expr(text, symbols):
    """Evaluate an integer expression of literals, symbols and + - * / % <<
    >> & | ^."""
    text = text.strip()
    if not text:
        raise AsmError("missing operand")

    # Substitute symbols by value first, so that names which happen to be Python
    # keywords (pass, if, ...) or contain dots are still usable.
    def substitute(match):
        token = match.group(0)
        if token[0].isdigit():
            return token
        if token not in symbols:
            raise AsmError(f"undefined symbol '{token}'")
        return f"({symbols[token]})"

    text = _TOKEN_RE.sub(substitute, text)
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        raise AsmError(f"cannot parse expression '{text}'")

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.USub):
                return -walk(node.operand)
            if isinstance(node.op, ast.Invert):
                return ~walk(node.operand)
            if isinstance(node.op, ast.UAdd):
                return walk(node.operand)
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
            return _ALLOWED_BINOPS[type(node.op)](walk(node.left), walk(node.right))
        raise AsmError(f"unsupported expression '{text}'")

    return walk(tree)


_MEM_RE = re.compile(r"^(.*)\(\s*([A-Za-z0-9_]+)\s*\)$")
_LABEL_RE = re.compile(r"^([A-Za-z_.][A-Za-z0-9_.$]*)\s*:")


def _strip_comment(line):
    for marker in ("#", "//", ";"):
        pos = line.find(marker)
        if pos >= 0:
            line = line[:pos]
    return line.strip()


def _split_operands(text):
    return [p.strip() for p in text.split(",")] if text.strip() else []


def _reg(name):
    key = name.strip().lower()
    if key not in REGS:
        raise AsmError(f"unknown register '{name}'")
    return REGS[key]


def _expect(ops, count, mnemonic):
    if len(ops) != count:
        raise AsmError(f"'{mnemonic}' takes {count} operand(s), got {len(ops)}")


# --------------------------------------------------------------------------
# The assembler
# --------------------------------------------------------------------------
class Assembler:
    def __init__(self):
        self.symbols = {}

    # -- size of a statement (pass 1) --------------------------------------
    def _size(self, mnemonic, ops, pc):
        if mnemonic == "li":
            _expect(ops, 2, "li")
            # li length depends on the value, so it must be known in pass 1.
            return 4 * len(li_sequence(0, eval_expr(ops[1], self.symbols)))
        if mnemonic == "la":
            return 8
        if mnemonic == ".byte":
            return len(ops)
        if mnemonic == ".half":
            return 2 * len(ops)
        if mnemonic == ".word":
            return 4 * len(ops)
        if mnemonic == ".dword":
            return 8 * len(ops)
        if mnemonic == ".zero":
            return eval_expr(ops[0], self.symbols)
        if mnemonic == ".align":
            step = 1 << eval_expr(ops[0], self.symbols)
            return (-pc) % step
        if mnemonic == ".org":
            target = eval_expr(ops[0], self.symbols)
            if target < pc:
                raise AsmError(f".org 0x{target:x} moves backwards from 0x{pc:x}")
            return target - pc
        return 4

    # -- encode one statement (pass 2) -------------------------------------
    def _mem_operand(self, text):
        match = _MEM_RE.match(text.strip())
        if not match:
            raise AsmError(f"expected offset(register), got '{text}'")
        offset = eval_expr(match.group(1), self.symbols) if match.group(1).strip() else 0
        return offset, _reg(match.group(2))

    def _imm12(self, text):
        value = eval_expr(text, self.symbols)
        if not _fits_signed(value, 12):
            raise AsmError(f"immediate {value} does not fit in 12 signed bits")
        return value

    def _csr(self, text):
        key = text.strip().lower()
        if key in CSRS:
            return CSRS[key]
        value = eval_expr(text, self.symbols)
        if not 0 <= value < 4096:
            raise AsmError(f"CSR address {value} out of range")
        return value

    def _target_offset(self, text, pc, bits):
        offset = eval_expr(text, self.symbols) - pc
        if offset & 1:
            raise AsmError(f"target offset {offset} is odd")
        if not _fits_signed(offset, bits):
            raise AsmError(f"target is out of range ({offset} bytes away)")
        return offset

    def _encode(self, mnemonic, ops, pc):
        """Return the bytes for one statement."""
        words = self._encode_words(mnemonic, ops, pc)
        if isinstance(words, (bytes, bytearray)):
            return bytes(words)
        return b"".join((w & 0xFFFFFFFF).to_bytes(4, "little") for w in words)

    def _encode_words(self, m, ops, pc):
        sym = self.symbols
        # ---- directives ----
        if m in (".byte", ".half", ".word", ".dword"):
            width = {".byte": 1, ".half": 2, ".word": 4, ".dword": 8}[m]
            mask = (1 << (8 * width)) - 1
            return b"".join((eval_expr(o, sym) & mask).to_bytes(width, "little")
                            for o in ops)
        if m in (".zero", ".align", ".org"):
            return bytes(self._size(m, ops, pc))
        # ---- real instructions ----
        if m in R_TYPE:
            _expect(ops, 3, m)
            opc, f3, f7 = R_TYPE[m]
            return [enc_r(opc, f3, f7, _reg(ops[0]), _reg(ops[1]), _reg(ops[2]))]
        if m in I_ARITH:
            _expect(ops, 3, m)
            opc, f3 = I_ARITH[m]
            return [enc_i(opc, f3, _reg(ops[0]), _reg(ops[1]), self._imm12(ops[2]))]
        if m in SHIFT_IMM:
            _expect(ops, 3, m)
            opc, f3, upper, width = SHIFT_IMM[m]
            shamt = eval_expr(ops[2], sym)
            if not 0 <= shamt < (1 << width):
                raise AsmError(f"shift amount {shamt} out of range for {m}")
            return [enc_i(opc, f3, _reg(ops[0]), _reg(ops[1]), upper | shamt)]
        if m in LOADS:
            _expect(ops, 2, m)
            offset, base = self._mem_operand(ops[1])
            if not _fits_signed(offset, 12):
                raise AsmError(f"offset {offset} does not fit in 12 signed bits")
            return [enc_i(LOAD, LOADS[m], _reg(ops[0]), base, offset)]
        if m in STORES:
            _expect(ops, 2, m)
            offset, base = self._mem_operand(ops[1])
            if not _fits_signed(offset, 12):
                raise AsmError(f"offset {offset} does not fit in 12 signed bits")
            return [enc_s(STORE, STORES[m], base, _reg(ops[0]), offset)]
        if m in BRANCHES:
            _expect(ops, 3, m)
            offset = self._target_offset(ops[2], pc, 13)
            return [enc_b(BRANCH, BRANCHES[m], _reg(ops[0]), _reg(ops[1]), offset)]
        if m == "jal":
            if len(ops) == 1:                       # jal label  ==  jal ra, label
                ops = ["x1", ops[0]]
            _expect(ops, 2, m)
            return [enc_j(JAL, _reg(ops[0]), self._target_offset(ops[1], pc, 21))]
        if m == "jalr":
            if len(ops) == 1:                       # jalr rs  ==  jalr ra, 0(rs)
                return [enc_i(JALR, 0, 1, _reg(ops[0]), 0)]
            if len(ops) == 2:                       # jalr rd, off(rs)
                offset, base = self._mem_operand(ops[1])
                rd = _reg(ops[0])
            else:                                   # jalr rd, rs, off
                _expect(ops, 3, m)
                rd, base, offset = _reg(ops[0]), _reg(ops[1]), eval_expr(ops[2], sym)
            if not _fits_signed(offset, 12):
                raise AsmError(f"offset {offset} does not fit in 12 signed bits")
            return [enc_i(JALR, 0, rd, base, offset)]
        if m in ("lui", "auipc"):
            _expect(ops, 2, m)
            imm = eval_expr(ops[1], sym)
            if not -(1 << 19) <= imm < (1 << 20):
                raise AsmError(f"upper immediate {imm} does not fit in 20 bits")
            return [enc_u(LUI if m == "lui" else AUIPC, _reg(ops[0]), imm)]
        if m in CSR_OPS:
            _expect(ops, 3, m)
            f3 = CSR_OPS[m]
            if f3 >= 5:                             # immediate forms: 5-bit zimm
                src = eval_expr(ops[2], sym)
                if not 0 <= src < 32:
                    raise AsmError(f"CSR immediate {src} out of range 0..31")
            else:
                src = _reg(ops[2])
            return [(self._csr(ops[1]) << 20) | (src << 15) | (f3 << 12) |
                    (_reg(ops[0]) << 7) | SYSTEM]
        if m in FIXED:
            _expect(ops, 0, m)
            return [FIXED[m]]
        # ---- pseudo-instructions that expand to several words ----
        if m == "li":
            _expect(ops, 2, m)
            return li_sequence(_reg(ops[0]), eval_expr(ops[1], sym))
        if m == "la":
            _expect(ops, 2, m)
            rd = _reg(ops[0])
            offset = eval_expr(ops[1], sym) - pc
            lo = sext(offset, 12)
            hi = (offset - lo) >> 12
            if not _fits_signed(hi, 20):
                raise AsmError("la target out of range")
            return [enc_u(AUIPC, rd, hi), enc_i(OPIMM, 0, rd, rd, lo)]
        raise AsmError(f"unknown instruction or directive '{m}'")

    # -- driver -------------------------------------------------------------
    def _statements(self, source):
        """Yield (lineno, raw line, label or None, mnemonic or None,
        operands)."""
        for lineno, raw in enumerate(source.splitlines(), 1):
            line = _strip_comment(raw)
            label = None
            match = _LABEL_RE.match(line)
            if match:
                label = match.group(1)
                line = line[match.end():].strip()
            if not line:
                yield lineno, raw, label, None, []
                continue
            parts = line.split(None, 1)
            mnemonic = parts[0].lower()
            ops = _split_operands(parts[1] if len(parts) > 1 else "")
            if mnemonic in REWRITES:
                arity, template = REWRITES[mnemonic]
                if len(ops) != arity:
                    raise AsmError(f"'{mnemonic}' takes {arity} operand(s)", lineno, raw)
                rewritten = template.format(*ops).split(None, 1)
                mnemonic, ops = rewritten[0], _split_operands(rewritten[1])
            yield lineno, raw, label, mnemonic, ops

    def assemble(self, source):
        """Assemble source text."""
        self.symbols = {}
        statements = []
        pc = 0
        # Pass 1: assign addresses to labels.
        for lineno, raw, label, mnemonic, ops in self._statements(source):
            try:
                if label is not None:
                    if label in self.symbols:
                        raise AsmError(f"symbol '{label}' defined twice")
                    self.symbols[label] = pc
                if mnemonic is None:
                    continue
                if mnemonic in (".equ", ".set"):
                    _expect(ops, 2, mnemonic)
                    self.symbols[ops[0]] = eval_expr(ops[1], self.symbols)
                    continue
                if mnemonic in (".text", ".data", ".globl", ".global", ".section"):
                    continue                        # accepted and ignored
                statements.append((lineno, raw, mnemonic, ops, pc))
                pc += self._size(mnemonic, ops, pc)
            except AsmError as err:
                raise AsmError(err.msg, lineno, raw) from None
        # Pass 2: encode.
        image = bytearray()
        listing = []
        for lineno, raw, mnemonic, ops, addr in statements:
            try:
                data = self._encode(mnemonic, ops, addr)
            except AsmError as err:
                raise AsmError(err.msg, lineno, raw) from None
            assert len(image) == addr, "pass 1 / pass 2 size mismatch"
            image += data
            listing.append((addr, data, raw))
        return bytes(image), listing, dict(self.symbols)


def assemble(source):
    """Assemble source text and return (image, listing, symbols)."""
    return Assembler().assemble(source)


def assemble_file(path):
    with open(path, encoding="utf-8") as handle:
        return assemble(handle.read())


def write_hex(image, path):
    """One hex byte per line, for $readmemh into a byte array."""
    with open(path, "w", encoding="ascii") as handle:
        handle.write("\n".join(f"{b:02x}" for b in image) + "\n")


def format_listing(listing):
    lines = []
    for addr, data, raw in listing:
        if len(data) % 4 == 0 and 0 < len(data) <= 32:
            words = [int.from_bytes(data[i:i + 4], "little")
                     for i in range(0, len(data), 4)]
            lines.append(f"{addr:08x}  {words[0]:08x}  {raw.rstrip()}")
            for k, word in enumerate(words[1:], 1):
                lines.append(f"{addr + 4 * k:08x}  {word:08x}")
        else:
            shown = data[:8].hex() + ("..." if len(data) > 8 else "")
            lines.append(f"{addr:08x}  {shown:<8}  {raw.rstrip()}")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="RV64I + Zicsr assembler")
    parser.add_argument("source")
    parser.add_argument("-o", "--output", help="output .hex (one byte per line)")
    parser.add_argument("--lst", help="write an address/encoding listing")
    args = parser.parse_args(argv)
    try:
        image, listing, _ = assemble_file(args.source)
    except AsmError as err:
        print(f"{args.source}: {err}", file=sys.stderr)
        return 1
    if args.output:
        write_hex(image, args.output)
    if args.lst:
        with open(args.lst, "w", encoding="utf-8") as handle:
            handle.write(format_listing(listing))
    if not args.output and not args.lst:
        sys.stdout.write(format_listing(listing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
