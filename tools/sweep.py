#!/usr/bin/env python3
"""sweep - design-space sweeps of the cache: analyses, tables and charts."""

import hashlib
import heapq
import json
import math
import os
from collections import OrderedDict

import cachesim
import rvasm
import rvsim
import svgplot

LINE = 32
SIZES = (256, 512, 1024, 2048, 4096, 8192, 16384)   # bytes, model
WAYS = (1, 2, 4, 8, "full")
RTL_SIZES = (256, 512, 1024, 2048, 4096)            # bytes, RTL grid
RTL_WAYS = (1, 2, 4)
LINES = (8, 16, 32, 64, 128)                        # line sizes, bytes
BASE = "g-1024-4"           # the machine every other RTL group varies
REPLACEMENT = ("lru", "fifo", "random", "plru")
REPL_CODES = {"lru": cachesim.LRU, "fifo": cachesim.FIFO, "random": cachesim.RANDOM,
              "plru": cachesim.PLRU}
REPL_SIZES = (512, 1024, 2048, 4096, 8192)          # 4 ways each
WRITE = (("w-wb-a", "write-back, allocate"), ("w-wt-a", "write-through, allocate"),
         ("w-wb-na", "write-back, no-allocate"), ("w-wt-na", "write-through, no-allocate"))
IC_SIZES = (256, 512, 1024, 2048, 4096)
L2_KIB = (1, 2, 4, 8, 16, 32)
MIN_ACCESSES = 1000         # kernels with fewer data accesses are left out
# Counters the model on its own must reproduce exactly.
TIED = ("access", "miss", "evict", "writeback", "mem_read", "mem_write", "store_miss")
# Counters of an RTL run that are kept in the data file.
KEPT = ("cycles", "instret", "hold", "ifwait", "dc_access", "dc_miss", "dc_mem_read",
        "dc_mem_write", "dc_writeback", "dc_evict", "dc_store_miss", "dc_wait", "dc_wbuf_full",
        "dc_inval", "ic_access", "ic_miss", "ic_evict", "ic_mem_read", "ic_wait", "ic_inval",
        "l2_access", "l2_miss", "l2_store_miss", "l2_evict", "l2_mem_read", "l2_mem_write",
        "l2_writeback")


# --------------------------------------------------------------------------
# Recording a kernel's accesses
# --------------------------------------------------------------------------
class Recorder(rvsim.Simulator):
    """The golden model, also noting every access to memory."""

    def __init__(self, image):
        super().__init__(image)
        self.data = []          # (address, 1 for a store) in program order
        self.fetch = []         # address of every instruction executed

    def step(self):
        if self.pc % 4 == 0 and self.pc + 4 <= len(self.mem):
            self.fetch.append(self.pc)
        super().step()

    def load(self, addr, size):
        value = super().load(addr, size)
        self.data.append((addr, 0))
        return value

    def store(self, addr, size, value):
        in_ram = super().store(addr, size, value)
        if in_ram:
            self.data.append((addr, 1))
        return in_ram


def record(path, max_instructions=2_000_000):
    """Run a program on the golden model."""
    image, _, _ = rvasm.assemble_file(path)
    sim = Recorder(image)
    if sim.run(max_instructions) != "halt" or sim.exit_code != 0:
        raise RuntimeError(f"{path}: the golden model did not finish with exit code 0")
    return sim


def source_hash(path):
    """Checksum of a program as assembled; comments and layout do not change
    it."""
    image, _, _ = rvasm.assemble_file(path)
    return hashlib.sha256(bytes(image)).hexdigest()[:16]


# --------------------------------------------------------------------------
# Simulators
# --------------------------------------------------------------------------
def lines_of(addresses, line_bytes=LINE):
    return [addr // line_bytes for addr in addresses]


def shape(size, ways, line_bytes=LINE):
    """(sets, ways) of a cache of `size` bytes; ways may be "full"."""
    total = size // line_bytes
    return (1, total) if ways == "full" else (total // ways, ways)


def lru_misses(lines, sets, ways):
    """Misses of an LRU cache on a sequence of line numbers."""
    table = [OrderedDict() for _ in range(sets)]
    misses = 0
    for line in lines:
        entry = table[line % sets]
        if line in entry:
            entry.move_to_end(line)
        else:
            misses += 1
            if len(entry) >= ways:
                entry.popitem(last=False)
            entry[line] = True
    return misses


def opt_misses(lines, sets, ways):
    """Misses of the optimal replacement policy: on a miss in a full set,
    evict the line whose next use is furthest away."""
    never = len(lines)
    next_use = [never] * len(lines)
    seen = {}
    for index in range(len(lines) - 1, -1, -1):
        next_use[index] = seen.get(lines[index], never)
        seen[lines[index]] = index
    content = [{} for _ in range(sets)]         # line -> its next use
    order = [[] for _ in range(sets)]           # heap of (-next use, line); stale entries stay
    misses = 0
    for index, line in enumerate(lines):
        held, heap = content[line % sets], order[line % sets]
        if line not in held:
            misses += 1
            if len(held) >= ways:
                while True:
                    use, victim = heapq.heappop(heap)
                    if held.get(victim) == -use:
                        del held[victim]
                        break
        held[line] = next_use[index]
        heapq.heappush(heap, (-next_use[index], line))
    return misses


def classify(lines, sets, ways):
    compulsory = len(set(lines))
    floor = opt_misses(lines, 1, sets * ways)
    return compulsory, floor - compulsory, lru_misses(lines, sets, ways) - floor


def model_counters(accesses, **params):
    """The reference model on its own: every counter after `accesses`, which
    are (address, 1 for a store)."""
    cache = cachesim.Cache(**params)
    for addr, write in accesses:
        cache.access(addr, write)
    return cache.count


def replay_fetches(log_lines, **params):
    cache = cachesim.Cache(**params)
    for text in log_lines:
        if text.startswith(("F r ", "H r ")):
            cache.access(int(text.split()[2], 16))
        elif text.startswith("X "):
            line = cache.line_of(int(text.split()[1], 16))
            way = cache.find(line)
            if way is not None:
                cache.line[cache.set_of(line)][way] = None
                cache.count["inval"] += 1
    return cache.count


def model_results(recorded):
    """Every number the model contributes. recorded: {kernel: Recorder}."""
    model = {"size": {}, "floor": {}, "line": {}, "repl": {}, "classes": {}, "fetch": {}}
    for kernel, sim in recorded.items():
        addresses = [addr for addr, _ in sim.data]
        lines = lines_of(addresses)
        if len(addresses) >= MIN_ACCESSES:
            model["size"][kernel] = {
                f"{size}-{ways}": lru_misses(lines, *shape(size, ways))
                for size in SIZES for ways in WAYS}
            model["floor"][kernel] = {str(size): opt_misses(lines, *shape(size, "full"))
                                      for size in SIZES}
            model["line"][kernel] = {
                str(line): lru_misses(lines_of(addresses, line), *shape(1024, 4, line))
                for line in LINES}
            model["repl"][kernel] = {}
            for size in REPL_SIZES:
                sets, ways = shape(size, 4)
                entry = {"opt": opt_misses(lines, sets, ways)}
                for name, code in REPL_CODES.items():
                    entry[name] = model_counters(sim.data, sets=sets, ways=ways,
                                                 repl=code)["miss"]
                model["repl"][kernel][str(size)] = entry
            model["classes"][kernel] = {str(ways): list(classify(lines, *shape(1024, ways)))
                                        for ways in (1, 2, 4)}
        fetched = lines_of(sim.fetch)
        model["fetch"][kernel] = {str(size): lru_misses(fetched, size // LINE, 1)
                                  for size in IC_SIZES}
    return model


def stream_facts(recorded):
    return {kernel: {"instr": sim.retired, "accesses": len(sim.data),
                     "stores": sum(write for _, write in sim.data),
                     "lines": len({addr // LINE for addr, _ in sim.data}),
                     "code_lines": len({addr // LINE for addr in sim.fetch})}
            for kernel, sim in recorded.items()}


# --------------------------------------------------------------------------
# The data file
# --------------------------------------------------------------------------
def dump(data):
    """The data file's text: one line per entry of each section."""
    out = ["{"]
    sections = sorted(data)
    for section in sections:
        out.append(f' {json.dumps(section)}: {{')
        keys = sorted(data[section])
        for key in keys:
            value = json.dumps(data[section][key], sort_keys=True, separators=(",", ":"))
            out.append(f"  {json.dumps(key)}: {value}" + ("," if key != keys[-1] else ""))
        out.append(" }" + ("," if section != sections[-1] else ""))
    out.append("}")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# The document
# --------------------------------------------------------------------------
def size_text(size, short=False):
    if size < 1024:
        return str(size) if short else f"{size} B"
    return f"{size // 1024}K" if short else f"{size // 1024} KiB"


def ways_text(ways):
    return {1: "direct-mapped", "full": "fully associative"}.get(ways, f"{ways}-way")


def percent(value):
    return f"{100 * value:.1f}%"


def tick_percent(value):
    text = f"{100 * value:.1f}"
    return (text[:-2] if text.endswith(".0") else text) + "%"


def ratio(value):
    return f"{value:.2f}"


def geomean(values):
    values = list(values)
    return math.exp(sum(map(math.log, values)) / len(values)) if values else 1.0


def table(header, rows, align=None):
    align = align or ["---"] + ["---:"] * (len(header) - 1)
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join(align) + " |"]
    out += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return out


def document(data):
    """Returns (markdown, {chart file name: SVG text})."""
    model, rtl, facts = data["model"], data["rtl"], data["streams"]
    agreement = data["agreement"]["summary"]
    kernels = sorted(model["size"])                 # kernels with data accesses
    everything = sorted(facts)
    charts = {}
    total = sum(facts[k]["accesses"] for k in kernels)

    def pooled(pick):
        """Misses of all kernels together, per data access."""
        return sum(pick(k) for k in kernels) / total

    def cycles(config, kernel):
        return rtl[config][kernel]["cycles"]

    def relative(config, base, among=None):
        return geomean(cycles(config, k) / cycles(base, k) for k in (among or kernels))

    out = [
        "# Design-space sweeps", "",
        "<!-- Generated by `python tools/regress.py sweep`. Do not edit: change",
        "tools/sweep.py and run it again. -->", "",
        "What the cache's parameters do, one at a time: its size, its",
        "associativity, the size of a line, the replacement policy, the write",
        "policy, and the size and inclusion of a second level. Every table and",
        "chart here is written by one command, `python tools/regress.py sweep`.",
        "What the numbers mean for the design is in [RESULTS.md](RESULTS.md).", "",
        "## How the numbers are made", "",
        "Two engines produce them.", "",
        "- **The model** gives misses. Each kernel is run once on the golden model,",
        "  which notes every load and store. That sequence of accesses is then",
        "  replayed through a simulator of the cache, which takes a few",
        "  milliseconds per configuration and can also do what no hardware can:",
        "  evict the line that will be used furthest in the future (the optimal",
        "  policy, Belady's).",
        "- **The RTL** gives cycles. The in-order core runs every kernel on a",
        f"  smaller grid of {len(rtl)} machines, with the predictor of `bp2`, no",
        "  prefetchers and a 1 KiB direct-mapped instruction cache unless the",
        "  table says otherwise. Every run is checked as in the regression: commit",
        "  trace, final memory image and the caches' event logs.", "",
        "The two must agree exactly. At every point of the RTL grid the reference",
        "model (`tools/cachesim.py`) replays the same kernel on its own, from the",
        "golden model's accesses, knowing nothing of what the RTL did, and its",
        "counters are compared with the RTL's:", ""]
    out += table(["What is compared", "Runs", "Agree"], [
        ["Data cache: accesses, misses, evictions, write-backs, lines read from memory, "
         "writes to memory, stores that missed", agreement["data_points"],
         agreement["data_points"]],
        ["Instruction cache: misses, evictions, lines read from memory and lines taken "
         "back, the model replaying the fetches the RTL made", agreement["fetch_points"],
         agreement["fetch_points"]]],
        ["---", "---:", "---:"])
    out += [
        "",
        "The data side is compared wherever the model applies: every run without an",
        "inclusive second level, which takes lines out of the data cache for",
        "reasons the model of one cache cannot know. The instruction side cannot",
        "be replayed from the golden model at all, because fetch also reads",
        "instructions down mispredicted paths that the golden model never sees;",
        "there the model replays the fetches recorded in the RTL's own log and",
        "must miss exactly where the RTL did.",
        "",
        f"Lines are {LINE} bytes unless stated. The data-side sweeps use the "
        f"{len(kernels)} kernels",
        f"with at least {MIN_ACCESSES} data accesses:", ""]
    out += table(["Kernel", "Instructions", "Data accesses", "Stores", "Lines touched"],
                 [[f"`{k}`", facts[k]["instr"], facts[k]["accesses"], facts[k]["stores"],
                   facts[k]["lines"]] for k in kernels])

    # ---- size and associativity ------------------------------------------
    def misses(kernel, size, ways):
        return model["size"][kernel][f"{size}-{ways}"]

    out += ["", "## Cache size and associativity", "",
            "Model, LRU replacement. Miss rate of all data accesses of the kernels",
            "together. The last column is the floor: a fully associative cache with",
            "optimal replacement.", "",
            "![Miss rate against cache size](img/sweep_size.svg)", ""]
    rows = []
    for size in SIZES:
        rows.append([size_text(size)]
                    + [percent(pooled(lambda k, w=ways: misses(k, size, w))) for ways in WAYS]
                    + [percent(pooled(lambda k: model["floor"][k][str(size)]))])
    out += table(["Data cache"] + [ways_text(w).capitalize() for w in WAYS]
                 + ["Fully associative, optimal"], rows)
    ramp = ["o1", "o2", "o3", "o4", "o5"]
    series = [(ways_text(w), ramp[i],
               [pooled(lambda k, s=size, w=w: misses(k, s, w)) for size in SIZES])
              for i, w in enumerate(WAYS)]
    charts["sweep_size.svg"] = svgplot.figure(
        [svgplot.line_panel([size_text(s) for s in SIZES], series, width=760, height=340,
                            y_title="Misses per data access", x_title="Data cache size",
                            y_fmt=tick_percent, end_labels=False)],
        title="Miss rate against cache size, by associativity",
        subtitle=f"All data accesses of {len(kernels)} kernels together; LRU, 32-byte lines; "
                 "replayed by the cache model.",
        legend=[(name, colour, "line") for name, colour, _ in series])

    shown = [(1, "o1"), (4, "o3"), ("full", "o5")]
    panels = []
    for kernel in kernels:
        accesses = facts[kernel]["accesses"]
        panels.append(svgplot.line_panel(
            [size_text(s, short=True) for s in SIZES],
            [(ways_text(w), colour, [misses(kernel, s, w) / accesses for s in SIZES])
             for w, colour in shown],
            width=228, height=150, title=kernel, y_fmt=tick_percent, end_labels=False))
    charts["sweep_size_kernels.svg"] = svgplot.figure(
        panels, cols=5, gap=18,
        title="Miss rate against cache size, kernel by kernel",
        subtitle="Misses per data access; LRU, 32-byte lines. Sizes from 256 bytes to 16 KiB. "
                 "Each kernel has its own vertical scale.",
        legend=[(ways_text(w), colour, "line") for w, colour in shown])
    out += ["", "Kernel by kernel, each on its own scale:", "",
            "![Miss rate against cache size, per kernel](img/sweep_size_kernels.svg)", "",
            "Misses at 1 KiB:", ""]
    out += table(["Kernel"] + [ways_text(w).capitalize() for w in WAYS]
                 + ["Fully associative, optimal"],
                 [[f"`{k}`"] + [misses(k, 1024, w) for w in WAYS] + [model["floor"][k]["1024"]]
                  for k in kernels])

    # More associativity is not always better.
    worse = []
    for kernel in kernels:
        cases = [(misses(kernel, size, b) - misses(kernel, size, a), size, a, b)
                 for size in SIZES for a, b in zip(WAYS, WAYS[1:])
                 if shape(size, a) != shape(size, b)
                 and misses(kernel, size, b) > misses(kernel, size, a)]
        if cases:
            extra, size, a, b = max(cases)
            worse.append([f"`{kernel}`", len(cases),
                          f"{size_text(size)}: {misses(kernel, size, a)} misses "
                          f"{ways_text(a)}, {misses(kernel, size, b)} {ways_text(b)}"])
    out += ["", "At the same size, more ways are not always fewer misses. Each step is",
            "from one associativity to the next in the table above, at one size:", ""]
    out += (table(["Kernel", "Steps that added misses", "Largest"], worse, ["---", "---:", "---"])
            if worse else ["No kernel has such a step."])

    base = "g-1024-1"
    out += ["", "RTL: cycles relative to the 1 KiB direct-mapped data cache, geometric mean",
            f"over the same {len(kernels)} kernels (below 1.00 is faster). Memory takes 10 "
            "cycles per line.", "",
            "![Cycles against cache size](img/sweep_size_cycles.svg)", ""]
    out += table(["Data cache"] + [ways_text(w).capitalize() for w in RTL_WAYS],
                 [[size_text(size)] + [ratio(relative(f"g-{size}-{w}", base)) for w in RTL_WAYS]
                  for size in RTL_SIZES])
    series = [(ways_text(w), colour,
               [relative(f"g-{size}-{w}", base) for size in RTL_SIZES])
              for w, colour in zip(RTL_WAYS, ("o1", "o3", "o5"))]
    charts["sweep_size_cycles.svg"] = svgplot.figure(
        [svgplot.line_panel([size_text(s) for s in RTL_SIZES], series, width=760, height=320,
                            y_title="Cycles relative to 1 KiB direct-mapped",
                            x_title="Data cache size", y_fmt=ratio, end_labels=False)],
        title="Cycles against data cache size, by associativity",
        subtitle=f"RTL, in-order core, memory latency 10; geometric mean over "
                 f"{len(kernels)} kernels.",
        legend=[(name, colour, "line") for name, colour, _ in series])

    # ---- line size ---------------------------------------------------------
    out += ["", "## Line size", "",
            "Model: a 1 KiB, 4-way LRU data cache divided into lines of each size, so",
            "larger lines mean fewer of them.", "",
            "![Miss rate against line size](img/sweep_line.svg)", ""]
    out += table(["Kernel"] + [f"{line} B" for line in LINES],
                 [[f"`{k}`"] + [percent(model["line"][k][str(line)] / facts[k]["accesses"])
                                for line in LINES] for k in kernels]
                 + [["All together"] + [percent(pooled(lambda k, l=line: model["line"][k][str(l)]))
                                        for line in LINES]])
    panels = [svgplot.line_panel(
        [str(line) for line in LINES],
        [("miss rate", "s1", [model["line"][k][str(line)] / facts[k]["accesses"]
                              for line in LINES])],
        width=228, height=150, title=k, y_fmt=tick_percent, end_labels=False) for k in kernels]
    charts["sweep_line.svg"] = svgplot.figure(
        panels, cols=5, gap=18,
        title="Miss rate against line size, kernel by kernel",
        subtitle="Misses per data access in a 1 KiB, 4-way LRU data cache; line size in bytes. "
                 "Each kernel has its own vertical scale.")
    out += ["",
            "RTL: cycles relative to 32-byte lines, both caches 1 KiB (the data cache",
            "4-way). Memory takes 10 cycles per line whatever the line's size, so in",
            "these runs a larger line is fetched for free; the table shows what the",
            "misses cost, not what the wider transfer would.", ""]
    out += table(["Kernel"] + [f"{line} B" for line in LINES],
                 [[f"`{k}`"] + [ratio(cycles(BASE if line == LINE else f"b-{line}", k)
                                      / cycles(BASE, k)) for line in LINES] for k in kernels]
                 + [["Geometric mean"]
                    + [f"**{ratio(relative(BASE if line == LINE else f'b-{line}', BASE))}**"
                       for line in LINES]])

    # ---- replacement -------------------------------------------------------
    names = {"opt": "Optimal", "lru": "LRU", "fifo": "FIFO", "random": "Random",
             "plru": "Pseudo-LRU"}
    order = ("opt",) + REPLACEMENT
    differ = [k for k in kernels if len(set(model["repl"][k]["1024"].values())) > 1]
    same = [k for k in kernels if k not in differ]
    out += ["", "## Replacement policy against the optimal one", "",
            "Model: data cache misses in 8 sets of 4 ways (1 KiB). The optimal policy",
            "evicts the line whose next use is furthest in the future; no cache can",
            "miss less often with the same sets and ways. The four real policies are",
            "also in the RTL grid, where their counters equal the model's.", "",
            "![Replacement policies](img/sweep_repl.svg)", ""]
    out += table(["Kernel", "Data accesses"] + [names[p] for p in order],
                 [[f"`{k}`", facts[k]["accesses"]] + [model["repl"][k]["1024"][p] for p in order]
                  for k in differ]
                 + [[f"All {len(kernels)} kernels", total]
                    + [sum(model["repl"][k]["1024"][p] for k in kernels) for p in order]])
    if same:
        out += ["", "Left out, because every policy including the optimal one misses equally",
                "often: " + ", ".join(f"`{k}`" for k in same) + "."]
    colours = {"opt": "n", "lru": "s1", "fifo": "s2", "random": "s3", "plru": "s4"}
    widest = sorted(differ, key=lambda k: -(max(model["repl"][k]["1024"].values())
                                            - model["repl"][k]["1024"]["opt"])
                    / facts[k]["accesses"])[:8]
    widest = [k for k in kernels if k in widest]
    charts["sweep_repl.svg"] = svgplot.figure(
        [svgplot.bar_panel(
            widest, [(names[p], colours[p],
                      [model["repl"][k]["1024"][p] / facts[k]["accesses"] for k in widest])
                     for p in order],
            width=900, height=330, y_title="Misses per data access", y_fmt=tick_percent)],
        title="Replacement policies against the optimal one",
        subtitle="Data cache of 8 sets x 4 ways (1 KiB); the kernels on which the policies "
                 "differ most. Grey is the floor no policy can go below.",
        legend=[(names[p], colours[p], "box") for p in order])
    out += ["", "The same comparison at other sizes (4 ways each): misses of all kernels",
            "together, relative to the optimal policy.", ""]
    out += table(["Data cache", "Optimal: misses"] + [names[p] for p in REPLACEMENT],
                 [[size_text(size), sum(model["repl"][k][str(size)]["opt"] for k in kernels)]
                  + [ratio(sum(model["repl"][k][str(size)][p] for k in kernels)
                           / sum(model["repl"][k][str(size)]["opt"] for k in kernels))
                     for p in REPLACEMENT] for size in REPL_SIZES])
    repl_config = {"lru": BASE, "fifo": "r-fifo", "random": "r-random", "plru": "r-plru"}
    out += ["", "RTL: cycles relative to LRU, at 8 sets of 4 ways.", ""]
    out += table(["Kernel"] + [names[p] for p in REPLACEMENT[1:]],
                 [[f"`{k}`"] + [ratio(cycles(repl_config[p], k) / cycles(BASE, k))
                                for p in REPLACEMENT[1:]] for k in differ]
                 + [[f"Geometric mean, all {len(kernels)} kernels"]
                    + [f"**{relative(repl_config[p], BASE):.3f}**" for p in REPLACEMENT[1:]]])

    # ---- classes of miss -----------------------------------------------------
    out += ["", "## Three kinds of miss", "",
            "Model, with the definitions of DDCA lecture 22. Compulsory: the first use",
            "of a line. Capacity: the further misses of a fully associative cache of",
            "the same size with optimal replacement. Conflict: every other miss of the",
            "cache itself. By these definitions what LRU loses to the optimal policy",
            "counts as conflict, too. A 1 KiB data cache, LRU.", "",
            "![Kinds of miss](img/sweep_classes.svg)", ""]
    rows = []
    for kernel in kernels:
        cells = [f"`{kernel}`", facts[kernel]["accesses"]]
        for ways in (1, 2, 4):
            cells.append(" / ".join(str(n) for n in model["classes"][kernel][str(ways)]))
        rows.append(cells)
    out += table(["Kernel", "Data accesses"]
                 + [f"{ways_text(w).capitalize()}: compulsory / capacity / conflict"
                    for w in (1, 2, 4)], rows)
    kinds = (("Compulsory", "s1"), ("Capacity", "s2"), ("Conflict", "s3"))
    panels = []
    for ways in (1, 4):
        panels.append(svgplot.hstack_panel(
            kernels, [(name, colour, [model["classes"][k][str(ways)][i] / facts[k]["accesses"]
                                      for k in kernels])
                      for i, (name, colour) in enumerate(kinds)],
            width=440, title=ways_text(ways).capitalize(), x_fmt=tick_percent, x_max=1.0,
            x_title="Misses per data access"))
    charts["sweep_classes.svg"] = svgplot.figure(
        panels, cols=2,
        title="Three kinds of miss in a 1 KiB data cache",
        subtitle="Share of each kernel's data accesses that miss, by the definitions of "
                 "DDCA lecture 22; LRU, 32-byte lines.",
        legend=[(name, colour, "box") for name, colour in kinds])

    # ---- write policy ----------------------------------------------------------
    storing = [k for k in kernels if rtl["w-wt-a"][k]["dc_mem_write"] >= 100]
    out += ["", "## Write policy", "",
            "RTL: the four combinations of what a store does on a hit (write-back or",
            "write-through) and on a miss (allocate the line or not), each with a",
            "4-entry write buffer that does not merge. Data cache of 8 sets x 4 ways.",
            "Kernels that send fewer than 100 writes to memory under write-through are",
            "left out. Cycles are relative to write-back with allocation.", "",
            "![Write policies](img/sweep_write.svg)", ""]
    out += table(["Kernel", "Stores"] + [f"{text.capitalize()}: cycles" for _, text in WRITE],
                 [[f"`{k}`", facts[k]["stores"]]
                  + [f"{cycles(c, k)} ({ratio(cycles(c, k) / cycles(WRITE[0][0], k))})"
                     for c, _ in WRITE] for k in storing]
                 + [[f"Geometric mean, all {len(kernels)} kernels", ""]
                    + [f"**{ratio(relative(c, WRITE[0][0]))}**" for c, _ in WRITE]])
    out += ["", "Traffic: writes sent to memory / lines read from memory. A write is a",
            "whole line when a dirty line is evicted and the bytes of one store",
            "otherwise. The model gives the same numbers.", ""]
    out += table(["Kernel"] + [text.capitalize() for _, text in WRITE],
                 [[f"`{k}`"] + [f"{rtl[c][k]['dc_mem_write']} / {rtl[c][k]['dc_mem_read']}"
                                for c, _ in WRITE] for k in storing])
    charts["sweep_write.svg"] = svgplot.figure(
        [svgplot.bar_panel(
            storing, [(text.capitalize(), colour,
                       [cycles(c, k) / cycles(WRITE[0][0], k) for k in storing])
                      for (c, text), colour in zip(WRITE, ("s1", "s2", "s3", "s4"))],
            width=900, height=330, y_title="Cycles relative to write-back, allocate",
            y_fmt=ratio, reference=1.0)],
        title="Write policies: cycles",
        subtitle="RTL, in-order core, memory latency 10, 4-entry write buffer; kernels that "
                 "store. Below 1.00 is faster than write-back with allocation.",
        legend=[(text.capitalize(), colour, "box")
                for (_, text), colour in zip(WRITE, ("s1", "s2", "s3", "s4"))])

    # ---- second level ------------------------------------------------------------
    none = "l-none"
    out += ["", "## Second-level cache: size and inclusion", "",
            "RTL: the slow-memory machine of the hierarchy results (memory takes 50",
            "cycles per line; 1 KiB first-level caches, the data cache 4-way) with a",
            "4-way second-level cache of each size that answers in 4 cycles. Cycles",
            f"are relative to the machine without a second level: geometric mean over "
            f"all {len(everything)}",
            "kernels, since the second level serves the instruction cache as well.",
            "Lines taken back are those an inclusive second level removed from a",
            "first-level cache in order to evict them itself, summed over the kernels.", "",
            "![Second-level cache](img/sweep_l2.svg)", ""]

    def taken(config):
        return sum(rtl[config][k]["ic_inval"] + rtl[config][k]["dc_inval"] for k in everything)

    out += table(["Second-level cache", "Non-inclusive", "Inclusive", "Inclusive / non-inclusive",
                  "Lines taken back"],
                 [[f"{kib} KiB", ratio(relative(f"l-{kib}-n", none, everything)),
                   ratio(relative(f"l-{kib}-i", none, everything)),
                   ratio(relative(f"l-{kib}-i", f"l-{kib}-n", everything)),
                   taken(f"l-{kib}-i")] for kib in L2_KIB])
    series = [("non-inclusive", "s1",
               [relative(f"l-{kib}-n", none, everything) for kib in L2_KIB]),
              ("inclusive", "s2", [relative(f"l-{kib}-i", none, everything) for kib in L2_KIB])]
    charts["sweep_l2.svg"] = svgplot.figure(
        [svgplot.line_panel([f"{kib} KiB" for kib in L2_KIB], series, width=760, height=320,
                            y_title="Cycles relative to no second level",
                            x_title="Second-level cache size", y_fmt=ratio, reference=1.0)],
        title="Cycles against second-level cache size",
        subtitle=f"RTL, in-order core, memory latency 50, 1 KiB first-level caches; geometric "
                 f"mean over {len(everything)} kernels. Below 1.00 is faster than no second "
                 "level.",
        legend=[(name, colour, "line") for name, colour, _ in series])
    for kind, title in (("n", "Non-inclusive"), ("i", "Inclusive")):
        out += ["", f"{title}, kernel by kernel:", ""]
        out += table(["Kernel", "No second level: cycles"] + [f"{kib} KiB" for kib in L2_KIB],
                     [[f"`{k}`", cycles(none, k)]
                      + [ratio(cycles(f"l-{kib}-{kind}", k) / cycles(none, k)) for kib in L2_KIB]
                      for k in everything])

    # ---- instruction cache ---------------------------------------------------------
    busy = [k for k in everything
            if max(rtl[BASE if size == 1024 else f"i-{size}"][k]["ic_miss"]
                   for size in IC_SIZES) >= 50]
    out += ["", "## Instruction cache", "",
            "A direct-mapped instruction cache of each size. Each cell is the misses",
            "of the model replaying the instructions the program executed, then the",
            "misses of the RTL, which also fetches down mispredicted paths. The",
            "difference is what those fetches add, or save when they bring a line in",
            "early. Kernels that never reach 50 misses are left out.", ""]
    out += table(["Kernel", "Code, in 32-byte lines"] + [size_text(size) for size in IC_SIZES],
                 [[f"`{k}`", facts[k]["code_lines"]]
                  + [f"{model['fetch'][k][str(size)]} / "
                     f"{rtl[BASE if size == 1024 else f'i-{size}'][k]['ic_miss']}"
                     for size in IC_SIZES] for k in busy])
    return "\n".join(out) + "\n", charts


def write_outputs(root, data):
    """Write the data file, the document and the charts."""
    markdown, charts = document(data)
    paths = {os.path.join(root, "docs", "data", "sweeps.json"): dump(data),
             os.path.join(root, "docs", "SWEEPS.md"): markdown}
    for name, svg in charts.items():
        paths[os.path.join(root, "docs", "img", name)] = svg
    for path, content in paths.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    return sorted(paths)


def save_data(root, data):
    """Write the data file alone."""
    path = os.path.join(root, "docs", "data", "sweeps.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(dump(data))
    return path
