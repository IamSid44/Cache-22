#!/usr/bin/env python3
"""figures - the charts and diagrams that tell the project's story.

    python tools/figures.py              # write docs/img/*.svg
    python tools/figures.py --collect    # first refresh docs/data/project.json
    python tools/figures.py --png FOLDER # also PNG copies of every figure
"""

import argparse
import ast
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import svgplot  # noqa: E402
from svgplot import num, text  # noqa: E402

DATA = os.path.join(ROOT, "docs", "data", "project.json")
SWEEPS = os.path.join(ROOT, "docs", "data", "sweeps.json")
IMG = os.path.join(ROOT, "docs", "img")

# Counters of a bench run that the charts use.
KEPT = ("cycles", "instret", "branches", "branch_mis", "jumps", "jump_mis", "dc_access",
        "dc_miss", "ic_miss")
# The commit at which each phase was finished, in the repository the project was
# developed in (several phases share one).
COMMITS = (("1", "145dbce"), ("3", "5b97a91"), ("5", "93efe68"), ("6", "3fed68a"),
           ("7", "5d6cabd"), ("8", "2110880"), ("9", "81a9df8"))
LAST_PHASE = "10"           # measured from the files as they are
BUG_GROUPS = (("Pipeline", 14), ("Exceptions and CSRs", 16), ("Memory stalls", 6),
              ("Cache", 8), ("Misprediction recovery", 6), ("Predictor policy", 9),
              ("Prefetching in the cache", 7), ("Prefetcher policy", 7),
              ("Out-of-order core", 29), ("Two instructions per cycle", 36),
              ("Cache policies that lose data", 14), ("Cache policies that only misbehave", 12),
              ("Between the cache levels", 9), ("Inclusion", 15), ("Victim buffer", 10))


# --------------------------------------------------------------------------
# Collecting the numbers
# --------------------------------------------------------------------------
def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def code_lines(text, python):
    """Lines that hold code: no blank lines, comments or docstrings."""
    if not python:
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        return sum(bool(line.split("//")[0].strip()) for line in text.splitlines())
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return sum(bool(line.strip()) and not line.strip().startswith("#")
                   for line in text.splitlines())
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                skip.update(range(first.lineno, first.end_lineno + 1))
    used = set()
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type in (tokenize.NAME, tokenize.OP, tokenize.NUMBER, tokenize.STRING):
            used.update(range(token.start[0], token.end[0] + 1))
    return len(used - skip)


def size_of(files, read):
    """Lines of code in each part of the project. files: paths; read(path):
    text."""
    def lines(prefixes, suffixes, without=()):
        return sum(code_lines(read(path), path.endswith(".py")) for path in files
                   if path.startswith(prefixes) and path.endswith(suffixes)
                   and not path.startswith(without))
    tests = ("tests/", "tools/tests/")      # where the unit tests have lived
    return {"rtl": lines("rtl/", (".sv",)), "tb": lines("tb/", (".sv", ".v")),
            "tools": lines("tools/", (".py",), without=tests), "tests": lines(tests, (".py",)),
            "directed": sum(p.startswith("sw/tests/") and p.endswith(".S") for p in files),
            "kernels": sum(p.startswith("sw/bench/") and p.endswith(".S") for p in files)}


def bugs_in(verification_text):
    found = re.findall(r"All (\d+) are currently caught", verification_text)
    return int(found[0]) if found else 0


def collect_history(recorded=()):
    """Sizes at the end of each phase. Without the commits, the recorded
    sizes of the earlier phases are kept and only the last one is measured."""
    history = []
    for phase, commit in COMMITS:
        files = git("ls-tree", "-r", "--name-only", commit).splitlines()
        if not files:
            history = [entry for entry in recorded if entry["phase"] != LAST_PHASE]
            if not history:
                return None
            break
        entry = size_of(files, lambda path, c=commit: git("show", f"{c}:{path}"))
        entry.update(phase=phase, bugs=bugs_in(git("show", f"{commit}:docs/VERIFICATION.md")))
        history.append(entry)
    files = []
    for folder in ("rtl", "tb", "tools", "sw"):
        for base, _, names in os.walk(os.path.join(ROOT, folder)):
            files += [os.path.relpath(os.path.join(base, name), ROOT).replace("\\", "/")
                      for name in names]

    def read(path):
        with open(os.path.join(ROOT, path), encoding="utf-8", errors="replace") as handle:
            return handle.read()

    entry = size_of(sorted(files), read)
    entry.update(phase=LAST_PHASE, bugs=bugs_in(read("docs/VERIFICATION.md")))
    return history + [entry]


def collect_bench():
    path = os.path.join(ROOT, "build", "bench_stats.json")
    if not os.path.exists(path):
        return None
    import regress
    with open(path, encoding="utf-8") as handle:
        saved = json.load(handle)
    runs = {key: {name: stats[name] for name in KEPT if name in stats}
            for key, stats in saved["table"].items()}
    stairs = {}
    for size in regress.WS_SIZES:
        for name in regress.STAIR_CONFIGS:
            short = saved["staircase"][f"{size}/{regress.WS_READS}@{name}"]
            long = saved["staircase"][f"{size}/{2 * regress.WS_READS}@{name}"]
            stairs[f"{size}@{name}"] = round(
                1 + (long["dc_wait"] - short["dc_wait"])
                / (long["dc_access"] - short["dc_access"]), 4)
    first = [k for k in saved["kernels"]
             if k not in regress.POLICY_KERNELS and k not in regress.HIER_KERNELS]
    return {"runs": runs, "staircase": stairs,
            "kernels": {"first": first, "all": saved["kernels"]}}


def collect_mutation(path):
    if not path or not os.path.exists(path):
        return None
    seen = {"results": 0, "model": 0, "invariant": 0, "blocks": 0}
    total = 0
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.startswith("CAUGHT"):
                if line.startswith(("MISSED", "INVALID")):
                    raise SystemExit(f"{path}: not the report of a clean run: {line.strip()}")
                continue
            total += 1
            tags = re.search(r"\[([^\]]*)\]\s*$", line)
            tags = {tag.strip() for tag in tags.group(1).split(",")} if tags else set()
            if tags & {"trace", "memory"}:
                seen["results"] += 1
            elif "model" in tags:
                seen["model"] += 1
            elif "invariant" in tags:
                seen["invariant"] += 1
            else:
                seen["blocks"] += 1
    if total != sum(count for _, count in BUG_GROUPS):
        raise SystemExit(f"{path}: {total} bugs, but the groups add up to "
                         f"{sum(count for _, count in BUG_GROUPS)}")
    return {"total": total, "seen": seen}


def collect(mutation_log):
    data = load() if os.path.exists(DATA) else {}
    for section, value in (("bench", collect_bench()), ("mutation", collect_mutation(mutation_log)),
                           ("history", collect_history(data.get("history", ())))):
        if value is None:
            print(f"{section}: source not available, kept as it was")
        else:
            data[section] = value
    os.makedirs(os.path.dirname(DATA), exist_ok=True)
    with open(DATA, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(dump(data))
    return data


def dump(data):
    """One line per run or entry, so that a change shows as a small diff."""
    def block(value, indent):
        pad = " " * indent
        if isinstance(value, dict) and value and all(isinstance(v, (dict, list))
                                                     for v in value.values()):
            keys = list(value)
            inner = [f"{pad} {json.dumps(key)}: {block(value[key], indent + 1)}"
                     + ("," if key != keys[-1] else "") for key in keys]
            return "{\n" + "\n".join(inner) + f"\n{pad}}}"
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            inner = [f"{pad} {json.dumps(item, separators=(',', ':'))}"
                     + ("," if index != len(value) - 1 else "")
                     for index, item in enumerate(value)]
            return "[\n" + "\n".join(inner) + f"\n{pad}]"
        return json.dumps(value, separators=(",", ":"))
    return block(data, 0) + "\n"


def load():
    with open(DATA, encoding="utf-8") as handle:
        return json.load(handle)


def load_sweeps():
    with open(SWEEPS, encoding="utf-8") as handle:
        return json.load(handle)


# --------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------
def geomean(values):
    values = list(values)
    return math.exp(sum(map(math.log, values)) / len(values))


def times(value):
    return f"{value:.1f}x"


def times_tick(value):
    return f"{value:.0f}x"


def ratio(value):
    return f"{value:.2f}"


def whole(value):
    return f"{value:,.0f}"


def charts(data):
    """{file name: SVG text} for every chart."""
    runs, kernels = data["bench"]["runs"], data["bench"]["kernels"]
    first, everything = kernels["first"], kernels["all"]
    out = {}

    def cycles(kernel, config):
        return runs[f"{kernel}@{config}"]["cycles"]

    def run(kernel, config):
        return runs[f"{kernel}@{config}"]

    def relative(kernel, config, base):
        return cycles(kernel, config) / cycles(kernel, base)

    # ---- the whole journey ------------------------------------------------
    steps = (("No caches: memory is 10 cycles away", "nocache"),
             ("+ instruction and data caches", "dm"),
             ("+ second cache way, branch predictor, prefetchers", "pf-stride"),
             ("+ out-of-order execution", "ooo-32"),
             ("+ two instructions per cycle", "ss"))
    speed = [geomean(cycles(k, "nocache") / cycles(k, config) for k in first)
             for _, config in steps]
    rows = [(steps[0][0], "n", speed[0], "the starting point")]
    rows += [(label, "s1", speed[i], f"{speed[i] / speed[i - 1]:.2f}x from this step")
             for i, (label, _) in enumerate(steps) if i]
    out["speedup.svg"] = svgplot.figure(
        [svgplot.hbar_panel(rows, width=860, x_fmt=times, tick_fmt=times_tick,
                            x_title="Times faster than the machine without caches")],
        title="What each step bought",
        subtitle=["Speed relative to the same pipeline with a 10-cycle memory and no caches. "
                  "Geometric mean over 15 kernels,",
                  "measured in clock cycles in RTL simulation; every run also matched the "
                  "golden model instruction for instruction."])

    ordered = sorted(first, key=lambda k: -cycles(k, "nocache") / cycles(k, "ss"))
    out["speedup_kernels.svg"] = svgplot.figure(
        [svgplot.hbar_panel([(k, "s1", cycles(k, "nocache") / cycles(k, "ss")) for k in ordered],
                            width=760, x_fmt=times, tick_fmt=times_tick, row_height=24,
                            x_title="Times faster than the machine without caches")],
        title="All steps together, kernel by kernel",
        subtitle="The finished machine (caches, predictor, prefetchers, out-of-order, two wide) "
                 "against the same pipeline without caches.")

    # ---- the staircase -----------------------------------------------------
    stairs = data["bench"]["staircase"]
    sizes = sorted({int(key.split("@")[0]) for key in stairs})
    names = (("h-l1", "first-level caches only", "s1"),
             ("h-l2", "with an 8 KiB second-level cache", "s2"),
             ("h-incl", "the same, inclusive", "s3"))
    series = [(label, colour, [stairs[f"{size}@{config}"] for size in sizes])
              for config, label, colour in names]
    out["staircase.svg"] = svgplot.figure(
        [svgplot.line_panel(
            [f"{s} B" if s < 1024 else f"{s // 1024} KiB" for s in sizes], series,
            width=780, height=340, y_title="Cycles per load", x_title="Working set",
            y_fmt=lambda v: f"{v:.0f}", end_labels=False)],
        title="The staircase: what a load costs as the working set grows",
        subtitle=["One loop reads a working set of each size round and round. A load costs 1 "
                  "cycle while the set fits the 1 KiB data cache,",
                  "7 while it fits the 8 KiB second level, and 59 when it has to come from "
                  "memory, 50 cycles away."],
        legend=[(label, colour, "line") for _, label, colour in names])

    # ---- branch prediction ---------------------------------------------------
    def wrong(kernel, config):
        stats = run(kernel, config)
        return 1000 * (stats["branch_mis"] + stats["jump_mis"]) / stats["instret"]

    branchy = [k for k in first if wrong(k, "2way") >= 60]
    predictors = (("2way", "no predictor", "n"), ("bp1", "last outcome (1 bit)", "s1"),
                  ("bp2", "2-bit counters", "s2"))
    out["branch.svg"] = svgplot.figure(
        [svgplot.bar_panel(branchy, [(label, colour, [wrong(k, config) for k in branchy])
                                     for config, label, colour in predictors],
                           width=860, height=320,
                           y_title="Wrong guesses per 1,000 instructions", y_fmt=whole)],
        title="Branch prediction: how often fetch goes the wrong way",
        subtitle="Each wrong guess costs two cycles. Without a predictor every taken branch and "
                 "every jump is one. Kernels with the most control flow.",
        legend=[(label, colour, "box") for _, label, colour in predictors])

    # ---- prefetching -----------------------------------------------------------
    moved = [k for k in first if abs(relative(k, "pf-next", "bp2") - 1) > 0.03
             or abs(relative(k, "pf-stride", "bp2") - 1) > 0.03]
    prefetchers = (("pf-next", "next-line", "s1"), ("pf-stride", "stride (for data)", "s2"))
    out["prefetch.svg"] = svgplot.figure(
        [svgplot.bar_panel(moved, [(label, colour, [relative(k, config, "bp2") for k in moved])
                                   for config, label, colour in prefetchers],
                           width=900, height=320, y_title="Cycles relative to no prefetching",
                           y_fmt=ratio, reference=1.0)],
        title="Prefetching: a gain where the pattern is learnable, a loss where it is not",
        subtitle="Below 1.00 is faster than the same machine without prefetchers. Kernels that "
                 "change by more than 3%.",
        legend=[(label, colour, "box") for _, label, colour in prefetchers])

    # ---- the cores ---------------------------------------------------------------
    cores = (("pf-stride", "in-order", "n"), ("ooo-32", "out-of-order, one wide", "s1"),
             ("ss", "out-of-order, two wide", "s2"))

    def ipc(kernel, config):
        stats = run(kernel, config)
        return stats["instret"] / stats["cycles"]

    half = (len(first) + 1) // 2
    top = max(ipc(k, "ss") for k in first)
    out["cores.svg"] = svgplot.figure(
        [svgplot.bar_panel(part, [(label, colour, [ipc(k, config) for k in part])
                                  for config, label, colour in cores],
                           width=900, height=250, y_title="Instructions per cycle",
                           y_fmt=ratio, y_max=top, reference=1.0)
         for part in (first[:half], first[half:])],
        title="Three cores on the same machine: instructions per cycle",
        subtitle="Same caches, predictor and prefetchers; only the core differs. The line is "
                 "one instruction per cycle, the ceiling of any core that is one wide.",
        legend=[(label, colour, "box") for _, label, colour in cores])

    # ---- the second level ------------------------------------------------------------
    levels = (("h-l2", "8 KiB second-level cache", "s1"), ("h-incl", "the same, inclusive", "s2"))
    half = (len(everything) + 1) // 2
    top = max(relative(k, config, "h-l1") for k in everything for config, _, _ in levels)
    out["hierarchy.svg"] = svgplot.figure(
        [svgplot.bar_panel(part, [(label, colour, [relative(k, config, "h-l1") for k in part])
                                  for config, label, colour in levels],
                           width=940, height=250,
                           y_title="Cycles relative to first-level caches only",
                           y_fmt=ratio, y_max=top, reference=1.0)
         for part in (everything[:half], everything[half:])],
        title="A second cache level in front of a slow memory",
        subtitle="Memory 50 cycles away. Below 1.00 is faster than the two 1 KiB first-level "
                 "caches by themselves; above it, the second level made things worse.",
        legend=[(label, colour, "box") for _, label, colour in levels])

    # ---- the first-level data cache ------------------------------------------------------
    def miss_rate(kernel, config):
        stats = run(kernel, config)
        return 100 * stats["dc_miss"] / stats["dc_access"]

    busy = [k for k in first if run(k, "dm")["dc_access"] >= 1000]
    shapes = (("dm", "direct-mapped", "s1"), ("2way", "two ways", "s2"))
    out["caches.svg"] = svgplot.figure(
        [svgplot.bar_panel(busy, [(label, colour, [miss_rate(k, config) for k in busy])
                                  for config, label, colour in shapes],
                           width=860, height=300, y_title="Data accesses that miss",
                           y_fmt=lambda v: f"{v:.0f}%", value_labels=True)],
        title="The data cache: what a second way can and cannot do",
        subtitle=["A 1 KiB data cache, direct-mapped and in two ways. conflict alternates "
                  "between two arrays that map to the same lines:",
                  "every access misses until both fit in a set. stride_walk walks 512 lines "
                  "in turn, far more than 1 KiB holds, so no arrangement helps."],
        legend=[(label, colour, "box") for _, label, colour in shapes])

    # ---- cache policies --------------------------------------------------------------------
    policy_kernels = [k for k in everything if f"{k}@lru" in runs]
    policies = (("fifo", "FIFO", "s2"), ("random", "random", "s3"), ("plru", "pseudo-LRU", "s4"))
    differ = [k for k in policy_kernels
              if any(abs(relative(k, config, "lru") - 1) > 0.015 for config, _, _ in policies)]
    out["policy_repl.svg"] = svgplot.figure(
        [svgplot.bar_panel(differ, [(label, colour, [relative(k, config, "lru") for k in differ])
                                    for config, label, colour in policies],
                           width=760, height=300, y_title="Cycles relative to LRU",
                           y_fmt=ratio, reference=1.0, value_labels=True)],
        title="Replacement policy: no policy is best everywhere",
        subtitle=["Cycles relative to LRU in a 1 KiB data cache of 8 sets x 4 ways; below 1.00 "
                  "is faster than LRU.",
                  f"The other {len(policy_kernels) - len(differ)} kernels are within 1.5% "
                  "under all four policies. lru_fifo was written for LRU, ways_plus_one "
                  "against it."],
        legend=[(label, colour, "box") for _, label, colour in policies])

    stores = (("wt", "write-through, no buffer", "s1"),
              ("wt-buf", "write-through, 4-entry write buffer", "s2"),
              ("wt-merge", "write-through, merging buffer", "s3"),
              ("wb-na-merge", "write-back, no allocation, merging buffer", "s4"))
    storing = [k for k in policy_kernels if relative(k, "wt", "lru") > 1.1]
    out["policy_write.svg"] = svgplot.figure(
        [svgplot.bar_panel(storing, [(label, colour, [relative(k, config, "lru")
                                                      for k in storing])
                                     for config, label, colour in stores],
                           width=900, height=300,
                           y_title="Cycles relative to write-back, allocate, no buffer",
                           y_fmt=ratio, reference=1.0)],
        title="Stores: what costs is waiting for memory, not the number of writes",
        subtitle=["The kernels that write-through slows by more than 10%. A write buffer "
                  "takes most of the wait away and merging the rest;",
                  "with merging, not fetching a line that is about to be overwritten takes "
                  "fill to 0.42."],
        legend=[(label, colour, "box") for _, label, colour in stores])

    # ---- where it helped most ------------------------------------------------------------
    cases = (("Second cache way on conflict", "conflict", "2way", "dm"),
             ("Victim buffer on conflict (direct-mapped)", "conflict", "dm-vb", "dm"),
             ("2-bit branch predictor on calls", "calls", "bp2", "2way"),
             ("2-bit branch predictor on loop_nest", "loop_nest", "bp2", "2way"),
             ("Stride prefetcher on array_sum", "array_sum", "pf-stride", "bp2"),
             ("Stride instead of next-line on stride_walk", "stride_walk", "pf-stride",
              "pf-next"),
             ("Instruction prefetcher on code_walk", "code_walk", "pf-next", "bp2"),
             ("Out-of-order core on miss_indep", "miss_indep", "ooo-32", "pf-stride"),
             ("Two-wide core on ilp_chains", "ilp_chains", "ss", "ooo-32"),
             ("LRU instead of FIFO on lru_fifo", "lru_fifo", "lru", "fifo"),
             ("Random instead of LRU on ways_plus_one", "ways_plus_one", "random", "lru"),
             ("Write buffer under write-through on store_hot", "store_hot", "wt-buf", "wt"),
             ("No allocation, merging write buffer on fill", "fill", "wb-na-merge", "lru"),
             ("Second-level cache on ws_walk", "ws_walk", "h-l2", "h-l1"),
             ("Victim buffer below a second level on incl_victim", "incl_victim", "h-vb",
              "h-l2"))
    rows = sorted(((label, "s1", cycles(kernel, base) / cycles(kernel, config))
                   for label, kernel, config, base in cases), key=lambda row: -row[2])
    out["gains.svg"] = svgplot.figure(
        [svgplot.hbar_panel(rows, width=860, x_fmt=lambda v: f"{v:.2f}x", tick_fmt=times_tick,
                            reference=1.0, row_height=26,
                            x_title="Times faster than the same machine without it "
                                    "(1x: no change)")],
        title="Where each component helped most",
        subtitle=["Each component on the kernel written to show what it is for, against the "
                  "same machine without that component.",
                  "The companion chart shows the same components on the kernels written to "
                  "show their limits."])

    # ---- where it did not help ---------------------------------------------------------
    cases = (("Two-wide core on code_walk", "code_walk", "ss", "ooo-32"),
             ("Second cache way on sieve", "sieve", "2way", "dm"),
             ("Out-of-order core on branch_alt", "branch_alt", "ooo-32", "pf-stride"),
             ("Second-level cache on stride_walk", "stride_walk", "h-l2", "h-l1"),
             ("Bigger reorder buffer on array_sum", "array_sum", "ooo-32", "ooo"),
             ("Next-line prefetcher on list_walk", "list_walk", "pf-next", "bp2"),
             ("Next-line prefetcher on stride_walk", "stride_walk", "pf-next", "bp2"),
             ("Inclusive second level (against none) on incl_victim", "incl_victim", "h-incl",
              "h-l1"),
             ("LRU instead of random on ways_plus_one", "ways_plus_one", "lru", "random"),
             ("FIFO instead of LRU on lru_fifo", "lru_fifo", "fifo", "lru"),
             ("Write-through without a buffer on store_hot", "store_hot", "wt", "lru"),
             ("Prefetchers above the second level on lru_fifo", "lru_fifo", "h-pf", "h-l2"),
             ("Prefetchers above the second level on incl_victim", "incl_victim", "h-pf",
              "h-l2"))
    rows = sorted(((label, "s2", relative(kernel, config, base))
                   for label, kernel, config, base in cases), key=lambda row: row[2])
    out["no_help.svg"] = svgplot.figure(
        [svgplot.hbar_panel(rows, width=860, x_fmt=ratio, reference=1.0, row_height=26,
                            x_title="Cycles relative to the same machine without it "
                                    "(1.00: no change; more: slower)")],
        title="Where a component did not help",
        subtitle=["Every component was measured on kernels built to show its limit as well as "
                  "its gain.",
                  "Each bar is one component on one kernel, against the same machine without "
                  "that component."])

    # ---- verification --------------------------------------------------------------------
    mutation = data["mutation"]
    seen = mutation["seen"]
    left = svgplot.hbar_panel([(name, "s1", count) for name, count in BUG_GROUPS], width=520,
                              title="What was broken", x_fmt=whole, row_height=22,
                              x_title="Planted bugs")
    right = svgplot.hbar_panel(
        [("A wrong result", "s1", seen["results"], "commit trace or memory image"),
         ("Only the cache model", "s2", seen["model"], "results were right"),
         ("Only a check inside the RTL", "s3", seen["invariant"], "results were right"),
         ("Only a block's own testbench", "s4", seen["blocks"], "results were right")],
        width=520, title="What noticed", x_fmt=whole, row_height=30, x_title="Planted bugs")
    out["planted_bugs.svg"] = svgplot.figure(
        [left, right], cols=2,
        title=f"{mutation['total']} bugs planted in the RTL, {mutation['total']} caught",
        subtitle=["A script breaks the design in one small way at a time and runs the tests. "
                  "A bug that no test notices is a gap in the tests.",
                  f"{mutation['total'] - seen['results']} of them change the result of no "
                  "program: only a second reference, or a check inside the design, can see "
                  "those."])

    # ---- the project over time ---------------------------------------------------------------
    history = data["history"]
    phases = [entry["phase"] for entry in history]

    def panel(title, series, legend=False):
        return svgplot.line_panel(phases, series, width=420, height=200, title=title,
                                  y_fmt=whole, end_labels=legend, x_title="Phase")

    out["growth.svg"] = svgplot.figure(
        [panel("The design: lines of RTL", [("RTL", "s1", [e["rtl"] for e in history])]),
         panel("What checks it: testbenches, tools and unit tests",
               [("checking", "s2", [e["tb"] + e["tools"] + e["tests"] for e in history])]),
         panel("Bugs planted to test the tests", [("bugs", "s3", [e["bugs"] for e in history])]),
         panel("Programs: directed tests and kernels",
               [("programs", "s4", [e["directed"] + e["kernels"] for e in history])])],
        cols=2,
        title="How the project grew",
        subtitle="Lines of code, without comments, at the end of each phase that has its own "
                 "commit. The code that checks the design is more than twice its size.")
    return out


# --------------------------------------------------------------------------
# Diagrams
# --------------------------------------------------------------------------
class Diagram:
    """A hand-placed drawing of boxes, labelled arrows and notes."""

    def __init__(self, width, height):
        self.width, self.height, self.parts = width, height, []
        self.labels = []                    # drawn last, over the arrows

    def box(self, x, y, w, h, title, lines=(), kind="box"):
        self.parts.append(f'<rect x="{num(x)}" y="{num(y)}" width="{num(w)}" height="{num(h)}" '
                          f'rx="6" class="{kind}"/>')
        rows = [title, *lines]
        top = y + h / 2 - (len(rows) - 1) * 7.5
        for index, row in enumerate(rows):
            self.parts.append(text(x + w / 2, top + 15 * index + 4, row, 12 if index == 0 else 11,
                                   "ink" if index == 0 else "ink2", "middle",
                                   600 if index == 0 else None))

    def arrow(self, points, label=None, at=None, anchor="middle", kind="wire", head=True):
        """A line through the points, with an arrowhead at the end."""
        path = " ".join(f'{"M" if i == 0 else "L"}{num(x)},{num(y)}'
                        for i, (x, y) in enumerate(points))
        marker = (f' marker-end="url(#head{"-a" if kind == "bus" else ""})"') if head else ""
        self.parts.append(f'<path d="{path}" class="{kind}"{marker}/>')
        if label:
            if at is None:
                (x0, y0), (x1, y1) = points[0], points[1]
                at = ((x0 + x1) / 2, (y0 + y1) / 2 - 6)
            self.note(at[0], at[1], label, anchor)

    def note(self, x, y, content, anchor="middle", colour="ink2", size=11, weight=None):
        self.labels.append(text(x, y, content, size, colour, anchor, weight, halo=True))

    def band(self, x, y, w, h, label):
        """A lightly drawn region with its name at the top left."""
        self.parts.insert(0, f'<rect x="{num(x)}" y="{num(y)}" width="{num(w)}" '
                             f'height="{num(h)}" rx="8" class="band"/>')
        self.parts.insert(1, text(x + 10, y + 16, label, 11, "muted"))

    def panel(self):
        def marker(name, kind, size):
            return (f'<marker id="{name}" viewBox="0 0 10 10" refX="9" refY="5" '
                    f'markerUnits="userSpaceOnUse" markerWidth="{size}" markerHeight="{size}" '
                    f'orient="auto-start-reverse"><path d="M0,1 L10,5 L0,9 Z" '
                    f'class="{kind}"/></marker>')
        defs = "<defs>" + marker("head", "head", 10) + marker("head-a", "head-a", 14) + "</defs>"
        return svgplot.Panel(self.width, self.height,
                             defs + "".join(self.parts + self.labels))


def pipeline_diagram():
    d = Diagram(900, 356)
    stage = {}
    names = (("IF", "fetch"), ("ID", "decode, read registers"), ("EX", "ALU, decide branches"),
             ("MEM", "load, store, CSRs, traps"), ("WB", "write register"))
    for index, (name, does) in enumerate(names):
        x = 30 + 176 * index
        stage[name] = x
        d.box(x, 120, 140, 60, name, [does], "box-a" if name in ("EX", "MEM") else "box")
        if index:
            d.arrow([(x - 36, 150), (x, 150)])
    d.note(stage["WB"] + 70, 202, "commit report:", "middle", "muted")
    d.note(stage["WB"] + 70, 217, "one line per instruction", "middle", "muted")
    # Forwarding, above the stages.
    d.arrow([(stage["MEM"] + 50, 120), (stage["MEM"] + 50, 96), (stage["EX"] + 100, 96),
             (stage["EX"] + 100, 120)])
    d.arrow([(stage["WB"] + 50, 120), (stage["WB"] + 50, 80), (stage["EX"] + 70, 80),
             (stage["EX"] + 70, 120)])
    d.note(stage["MEM"] + 80, 72, "forward results to EX: no cycles lost", "middle")
    # Predictor.
    d.box(stage["IF"], 20, 140, 50, "Branch predictor", ["BTB + 2-bit counters"])
    d.arrow([(stage["IF"] + 40, 70), (stage["IF"] + 40, 120)], "next pc",
            at=(stage["IF"] + 48, 100), anchor="start")
    d.arrow([(stage["EX"] + 30, 120), (stage["EX"] + 30, 45), (stage["IF"] + 140, 45)],
            "train with what really happened", at=(stage["ID"] + 60, 38))
    # Redirects, below the stages.
    d.arrow([(stage["EX"] + 70, 180), (stage["EX"] + 70, 222), (stage["IF"] + 100, 222),
             (stage["IF"] + 100, 180)],
            "fetch went the wrong way: redirect, 2 instructions squashed",
            at=(stage["ID"] + 50, 238))
    d.arrow([(stage["MEM"] + 40, 180), (stage["MEM"] + 40, 262), (stage["IF"] + 70, 262),
             (stage["IF"] + 70, 180)],
            "trap or mret, taken in MEM: 3 instructions squashed",
            at=(stage["EX"] + 20, 278))
    # Caches.
    d.box(stage["IF"] - 10, 300, 120, 44, "Instruction cache", kind="box")
    d.arrow([(stage["IF"] + 25, 180), (stage["IF"] + 25, 300)], head=True)
    d.note(stage["IF"] + 118, 326, "miss: IF sends bubbles", "start", "muted")
    d.box(stage["MEM"] + 60, 300, 120, 44, "Data cache", kind="box")
    d.arrow([(stage["MEM"] + 110, 180), (stage["MEM"] + 110, 300)])
    d.note(stage["MEM"] + 188, 326, "miss: every stage holds", "start", "muted")
    return svgplot.figure(
        [d.panel()], title="The in-order core: five stages",
        subtitle="Only MEM and WB change architectural state, and they are the last two "
                 "stages. Precise exceptions rest on that.")


def ooo_diagram():
    d = Diagram(940, 360)
    d.band(20, 16, 300, 250, "in program order")
    d.band(340, 16, 480, 170, "out of order")
    d.band(340, 206, 580, 136, "in program order again")
    d.box(40, 100, 110, 60, "Fetch", ["2 per cycle", "predictor"])
    d.box(180, 100, 120, 60, "Rename", ["register \u2192 tag", "dispatch"], "box-a")
    d.arrow([(150, 130), (180, 130)])
    d.box(370, 66, 160, 50, "Reservation stations", ["oldest ready goes first"], "box-a")
    d.box(370, 126, 160, 50, "Load/store queue", ["loads wait for older stores"], "box-a")
    d.box(570, 66, 120, 50, "2 ALUs", ["branches decided"])
    d.box(570, 126, 120, 50, "Data cache", ["1 load per cycle"])
    d.arrow([(300, 120), (336, 120), (336, 91), (370, 91)])
    d.arrow([(300, 142), (336, 142), (336, 151), (370, 151)])
    d.arrow([(530, 91), (570, 91)])
    d.arrow([(530, 151), (570, 151)])
    # The common data bus.
    d.arrow([(690, 91), (720, 91)], head=False)
    d.arrow([(690, 151), (720, 151)], head=False)
    d.arrow([(720, 48), (720, 238)], kind="bus")
    d.note(730, 116, "result + tag", "start")
    d.note(730, 131, "common", "start", "muted")
    d.note(730, 146, "data bus", "start", "muted")
    d.arrow([(720, 48), (450, 48), (450, 66)], "wake up whoever waits for this tag",
            at=(585, 42))
    # Reorder buffer and retirement.
    d.box(370, 238, 400, 56, "Reorder buffer", ["one entry per instruction in flight, "
                                                "oldest at the head"], "box-a")
    d.arrow([(240, 160), (240, 266), (370, 266)], "allocate", at=(248, 258), anchor="start")
    d.box(800, 238, 110, 56, "Retire", ["registers,", "memory, CSRs"])
    d.arrow([(770, 266), (800, 266)])
    d.arrow([(855, 294), (855, 322), (95, 322), (95, 160)],
            "wrong path or trap, found at the head: discard everything younger, refetch",
            at=(356, 316), anchor="start")
    return svgplot.figure(
        [d.panel()], title="The out-of-order core",
        subtitle="Tomasulo's algorithm with a reorder buffer. Instructions enter and leave in "
                 "program order; in between each runs when its operands are ready.")


def memory_diagram():
    d = Diagram(940, 356)
    d.box(20, 70, 110, 190, "Core", ["fetch port", "", "", "", "", "data port"])
    d.box(190, 70, 190, 70, "Instruction cache", ["1 KiB", "next-line prefetcher"], "box-a")
    d.box(190, 170, 190, 90, "Data cache", ["1 KiB; 4 replacement policies",
                                            "write-back or write-through",
                                            "write buffer, victim buffer"], "box-a")
    d.arrow([(130, 105), (190, 105)])
    d.arrow([(130, 215), (190, 215)])
    d.box(440, 140, 90, 50, "Arbiter", ["one at a time"])
    d.arrow([(380, 105), (410, 105), (410, 155), (440, 155)])
    d.arrow([(380, 215), (410, 215), (410, 175), (440, 175)])
    d.box(590, 120, 150, 90, "Second-level cache", ["8 KiB, shared", "the same module",
                                                    "inclusive or not"], "box-a")
    d.arrow([(530, 165), (590, 165)])
    d.box(800, 120, 120, 90, "Memory", ["64 KiB", "50 cycles", "per line"])
    d.arrow([(740, 165), (800, 165)])
    d.arrow([(690, 120), (690, 40), (285, 40), (285, 70)],
            "if inclusive: takes a line back from above before evicting it", at=(488, 33))
    d.arrow([(640, 210), (640, 286), (285, 286), (285, 260)])
    # What a load costs, by where its line is.
    for x, cost, where in ((285, "1 cycle", "line in the data cache"),
                           (665, "7 cycles", "line in the second level"),
                           (860, "59 cycles", "line in memory")):
        d.note(x, 322, cost, "middle", "ink", 13, 600)
        d.note(x, 340, where, "middle", "muted")
    return svgplot.figure(
        [d.panel()], title="The memory hierarchy, and what a load costs at each level",
        subtitle="One cache module is used three times. Every choice in it is a parameter, so "
                 "each can be measured against the others on the same machine.")


def verification_diagram(bugs):
    d = Diagram(940, 392)
    d.box(20, 110, 150, 96, "A program", ["directed test,", "kernel, or random",
                                          "(built to hit hazards)"])
    d.box(240, 50, 200, 70, "Golden model", ["one instruction at a time;", "no pipeline, "
                                             "no caches"])
    d.box(240, 196, 200, 86, "The RTL, in simulation", ["3 cores, 30+ machines:", "odd caches, "
                                                         "random latency,", "garbage predictions"],
          "box-a")
    d.arrow([(170, 140), (205, 140), (205, 85), (240, 85)])
    d.arrow([(170, 176), (205, 176), (205, 239), (240, 239)])
    checks = (("Commit trace", "every instruction, register", "write and store: identical"),
              ("Final memory image", "every byte: identical", ""),
              ("Cache model", "replays each cache's log;", "every decision must agree"),
              ("Checks inside the RTL", "inclusion, fairness: hold", "in every cycle"))
    for index, (name, first, second) in enumerate(checks):
        y = 30 + 84 * index
        d.box(560, y, 200, 66, name, [line for line in (first, second) if line])
    for y in (63, 147):
        d.arrow([(440, 85), (500, 85), (500, y), (560, y)])
        d.arrow([(440, 220), (520, 220), (520, y + 12), (560, y + 12)])
    for y in (231, 315):
        d.arrow([(440, 258), (520, 258), (520, y), (560, y)])
    d.box(800, 110, 124, 150, "Mutation check", [f"{bugs} bugs planted", "in the RTL,",
                                                  "one at a time.", "Each must make",
                                                  "a check fail."])
    d.arrow([(862, 260), (862, 376), (340, 376), (340, 282)], "plants a bug", at=(600, 370))
    d.arrow([(760, 185), (800, 185)], "fails?", at=(780, 177))
    return svgplot.figure(
        [d.panel()], title="How every run is checked",
        subtitle="Correctness is defined once, by the golden model. What its trace cannot see "
                 "(which line a cache evicts, whether two caches agree) gets a second reference.")


def system_diagram():
    d = Diagram(940, 392)
    d.band(20, 16, 690, 254, "the processor: rtl/")
    d.band(730, 16, 190, 254, "the testbench: tb/")
    d.band(290, 284, 420, 92, "written by every run, for the checks")
    d.box(40, 44, 200, 46, "Branch predictor", ["64-entry BTB, 2-bit counters"])
    d.box(40, 110, 200, 150, "Core", ["in-order, 5 stages", "or", "out-of-order, 1 or 2 wide",
                                      "", "RV64I, precise traps"], "box-a")
    d.arrow([(140, 90), (140, 110)])
    d.box(310, 96, 170, 56, "Instruction cache", ["next-line prefetcher"])
    d.box(310, 186, 170, 74, "Data cache", ["prefetcher, write buffer,", "victim buffer"])
    d.arrow([(240, 124), (310, 124)], "fetch")
    d.arrow([(240, 223), (310, 223)], "load, store")
    d.box(540, 140, 150, 76, "Second-level cache", ["optional, shared,", "inclusive or not"])
    d.arrow([(480, 124), (510, 124), (510, 165), (540, 165)])
    d.arrow([(480, 223), (510, 223), (510, 191), (540, 191)])
    d.box(750, 140, 150, 76, "Memory", ["64 KiB,", "10 or 50 cycles per line"])
    d.arrow([(690, 178), (750, 178)])
    d.box(310, 312, 170, 54, "Commit trace", ["compared with the", "golden model"])
    d.box(540, 312, 150, 54, "Cache event logs", ["replayed by the", "cache model"])
    d.arrow([(140, 260), (140, 339), (310, 339)], "commit report", at=(222, 332))
    d.arrow([(440, 260), (440, 276), (600, 276), (600, 312)])
    d.arrow([(640, 216), (640, 312)])
    return svgplot.figure(
        [d.panel()], title="The whole machine",
        subtitle="Either core plugs into the same memory system, and everything in it is a "
                 "parameter: sizes, ways, policies, buffers, the second level, memory latency.")


def cache_diagram():
    d = Diagram(940, 396)
    d.note(70, 49, "address", "end", "muted")
    d.box(80, 28, 260, 34, "tag")
    d.box(340, 28, 150, 34, "set index")
    d.box(490, 28, 120, 34, "byte offset")
    d.box(250, 100, 240, 70, "One set: WAYS lines", ["each: valid, dirty, tag, 32 bytes"],
          "box-a")
    d.arrow([(415, 62), (415, 100)], "selects the set", at=(423, 85), anchor="start")
    d.arrow([(160, 62), (160, 135), (250, 135)], "compare tags", at=(168, 128),
            anchor="start")
    d.box(600, 100, 320, 70, "Hit: answered in the same cycle",
          ["a store marks the line dirty (write-back)", "or also goes to memory (write-through)"])
    d.arrow([(490, 135), (600, 135)], "match")
    # A miss, left to right.
    d.box(30, 220, 180, 70, "Victim buffer", ["has the line? swap: 1 cycle.",
                                              "It also takes evicted lines"])
    d.box(255, 220, 200, 70, "Choose a victim", ["an empty way, else by policy:",
                                                 "LRU, FIFO, random, pseudo-LRU"], "box-a")
    d.box(500, 220, 180, 70, "Write buffer", ["a dirty line that leaves is",
                                              "queued, or written back first"])
    d.box(720, 220, 200, 70, "Fetch the line", ["next level's latency + 2 cycles;",
                                                "other lines still answer"])
    d.arrow([(370, 170), (370, 200), (120, 200), (120, 220)], "no match: a miss",
            at=(245, 194))
    d.arrow([(210, 255), (255, 255)], "no", at=(232, 248))
    d.arrow([(455, 255), (500, 255)])
    d.arrow([(680, 255), (720, 255)])
    d.arrow([(820, 220), (820, 200), (470, 200), (470, 170)], "install as most recently used",
            at=(650, 194))
    d.box(720, 330, 200, 56, "Prefetcher", ["next line or stride:", "lines nobody waits for yet"])
    d.arrow([(820, 330), (820, 290)])
    d.note(30, 352, "Every access and eviction is written to an event log,", "start", "muted")
    d.note(30, 368, "and a model of the policies must agree with each one.", "start", "muted")
    return svgplot.figure(
        [d.panel()], title="Inside the cache: what happens to one access",
        subtitle="One module serves as instruction cache, data cache and second-level cache. "
                 "Each box in the lower row is a parameter and can be left out.")


def predictor_diagram():
    d = Diagram(940, 356)
    d.band(20, 16, 900, 216, "at fetch, from the pc alone")
    d.band(20, 244, 900, 100, "the 2-bit counter of one entry")
    d.box(40, 68, 50, 34, "pc")
    d.box(140, 50, 250, 70, "Table: 64 entries", ["indexed by low pc bits; each entry:",
                                                  "valid | tag | target | 2-bit counter"],
          "box-a")
    d.arrow([(90, 85), (140, 85)])
    d.box(470, 50, 190, 70, "Next pc", ["target, if the tag matches", "and the counter is 2 or 3;",
                                        "otherwise pc + 4"])
    d.arrow([(390, 85), (470, 85)])
    for index, line in enumerate(("A loop branch is taken many times,",
                                  "then not taken once, at the exit.",
                                  "That moves the counter from 3 to 2:",
                                  "the next run starts predicted right.")):
        d.note(690, 66 + 16 * index, line, "start", "muted")
    d.box(140, 160, 250, 60, "Train", ["with what really happened, from EX",
                                       "(out-of-order core: at retirement)"])
    d.arrow([(265, 160), (265, 120)])
    for index, line in enumerate(("taken: count up, store the target",
                                  "not taken: count down",
                                  "taken and no entry: take one over, counter at 3")):
        d.note(410, 178 + 16 * index, line, "start")
    states = ("0: strongly not taken", "1: weakly not taken", "2: weakly taken",
              "3: strongly taken")
    for index, name in enumerate(states):
        x = 56 + 226 * index
        d.box(x, 272, 150, 50, name, ["predict taken" if index >= 2 else "predict not taken"],
              "box-a" if index >= 2 else "box")
        if index:
            d.arrow([(x - 76, 288), (x, 288)], "taken", at=(x - 38, 282))
            d.arrow([(x, 306), (x - 76, 306)], "not taken", at=(x - 38, 320))
    return svgplot.figure(
        [d.panel()], title="The branch predictor",
        subtitle="A branch target buffer with a 2-bit counter per entry. A wrong answer costs "
                 "cycles, never correctness: the core checks every prediction.")


def life_diagram():
    d = Diagram(940, 350)
    d.band(12, 16, 180, 124, "in program order")
    d.band(196, 16, 548, 124, "out of order")
    d.band(748, 16, 180, 124, "in program order")
    steps = (("1. Dispatch", ["gets the next ROB entry;", "sources: the value,",
                              "or a tag to wait for"]),
             ("2. Wait", ["in a reservation station,", "watching the bus", "for its tags"]),
             ("3. Execute", ["oldest ready goes first;", "result on the bus", "in the same cycle"]),
             ("4. Complete", ["result stored in", "its ROB entry,", "marked done"]),
             ("5. Retire", ["at the ROB head:", "register, memory", "or trap"]))
    for index, (name, lines) in enumerate(steps):
        x = 20 + 184 * index
        d.box(x, 44, 164, 86, name, lines, "box-a" if index == 2 else "box")
        if index:
            d.arrow([(x - 20, 87), (x, 87)])
    d.band(12, 160, 916, 176, "one number links a producer to its consumers: the tag is the "
                              "producer's ROB entry")
    d.note(30, 202, "Example:  add a0, a1, a2  is in ROB entry 5;  sub a3, a0, a4  comes "
                    "after it and reads a0.", "start")
    boxes = ((30, 190, "Rename table", ["a0 is being computed", "by entry 5"], "box",
              "add is dispatched"),
             (250, 200, "Reservation station", ["sub: one operand waits", "for tag 5"], "box",
              "sub is dispatched"),
             (480, 190, "Common data bus", ["tag 5 + the value"], "box-a", "add executes"),
             (700, 210, "Reorder buffer", ["entry 5: value, done;", "at the head: a0 written"],
              "box", "add completes, then retires"))
    for x, w, name, lines, kind, when in boxes:
        d.box(x, 238, w, 62, name, lines, kind)
        d.note(x + w / 2, 230, when, "middle", "muted")
    d.arrow([(220, 269), (250, 269)])
    d.arrow([(480, 269), (450, 269)])
    d.arrow([(670, 269), (700, 269)])
    d.note(350, 320, "captures the value when the tag on the bus matches its own", "middle")
    return svgplot.figure(
        [d.panel()], title="The life of an instruction in the out-of-order core",
        subtitle="Five steps, and the indirection that makes them work: nothing is ever "
                 "searched for by register number once an instruction has been renamed.")


def pair_diagram():
    d = Diagram(940, 266)
    d.note(20, 46, "slot 0", "start", "ink", 12, 600)
    d.note(20, 62, "the older", "start", "muted")
    d.note(20, 116, "slot 1", "start", "ink", 12, 600)
    d.note(20, 132, "width 2 only", "start", "muted")
    d.note(20, 188, "slot 1 stays", "start", "ink2")
    d.note(20, 204, "behind when", "start", "ink2")
    stages = (("Fetch", "the instruction at pc", "pc + 4, same cache access",
               ["slot 0 is predicted taken,", "or is the last in its line"]),
              ("Rename, dispatch", "sources from the rename table", "also sees slot 0's write",
               ["there is no room for two,", "or it is a system instruction"]),
              ("Execute", "the oldest ready station", "the next oldest; a second bus",
               ["a load completes this cycle", "and takes the second bus"]),
              ("Retire", "the head of the reorder buffer", "the entry behind the head",
               ["it is a store or a system", "instruction, it trapped, or", "the head was "
                                                                            "mispredicted"]))
    for index, (name, zero, one, limits) in enumerate(stages):
        x = 110 + 205 * index
        d.box(x, 24, 180, 50, name, [zero])
        d.box(x, 94, 180, 50, name, [one], "box-a")
        if index:
            d.arrow([(x - 25, 49), (x, 49)])
            d.arrow([(x - 25, 119), (x, 119)])
        for row, line in enumerate(limits):
            d.note(x + 90, 188 + 16 * row, line, "middle")
    d.arrow([(405, 74), (405, 94)])
    d.note(110, 254, "At width 1 the second slot is never used: the same RTL with one "
                     "parameter changed.", "start", "muted")
    return svgplot.figure(
        [d.panel()], title="Two instructions per cycle: what the second slot may do",
        subtitle="Every stage handles a pair. Almost all of the work in going wide is deciding "
                 "when the second of a pair has to wait a cycle.")


def anomaly_chart(sweeps):
    size, repl = sweeps["model"]["size"], sweeps["model"]["repl"]

    def by_ways(kernel, cache):
        rows = [(f"{ways} way" + ("s" if ways > 1 else ""), "s1", size[kernel][f"{cache}-{ways}"])
                for ways in (1, 2, 4, 8)]
        return rows + [("fully associative", "n", size[kernel][f"{cache}-full"])]

    policies = (("LRU", "s1", "lru"), ("FIFO", "s2", "fifo"), ("pseudo-LRU", "s4", "plru"),
                ("random", "s3", "random"), ("optimal", "n", "opt"))
    panels = [svgplot.hbar_panel(by_ways("ways_plus_one", 1024), width=300, x_fmt=whole,
                                 row_height=26, title="More ways, more misses",
                                 x_title="ways_plus_one, 1 KiB cache: misses"),
              svgplot.hbar_panel(by_ways("stride_walk", 16384), width=300, x_fmt=whole,
                                 row_height=26, title="As large as the data, still missing",
                                 x_title="stride_walk, 16 KiB cache: misses"),
              svgplot.hbar_panel([(name, colour, repl["ways_plus_one"]["1024"][key])
                                  for name, colour, key in policies], width=300, x_fmt=whole,
                                 row_height=26, title="LRU as the worst choice",
                                 x_title="ways_plus_one, 8 sets x 4 ways: misses")]
    return svgplot.figure(
        panels, cols=3, title="Three results that go against the rule of thumb",
        subtitle=["Misses counted by the cache model, which reproduced the RTL's counters in "
                  "every run where the two were compared.",
                  "ways_plus_one uses five lines in turn (4,005 accesses); stride_walk reads "
                  "one value every 64 bytes, 512 lines in all (7,680 accesses)."])


def diagrams(data):
    return {"system.svg": system_diagram(), "core_inorder.svg": pipeline_diagram(),
            "core_ooo.svg": ooo_diagram(), "ooo_life.svg": life_diagram(),
            "ooo_pair.svg": pair_diagram(), "memory.svg": memory_diagram(),
            "cache.svg": cache_diagram(), "predictor.svg": predictor_diagram(),
            "verification.svg": verification_diagram(data["mutation"]["total"])}


def build(data, sweeps=None):
    """{file name: SVG text} for every figure."""
    return {**charts(data), "anomalies.svg": anomaly_chart(sweeps or load_sweeps()),
            **diagrams(data)}


# --------------------------------------------------------------------------
# PNG copies
# --------------------------------------------------------------------------
BROWSERS = (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "msedge", "google-chrome", "chromium", "chrome")


def find_browser():
    for candidate in BROWSERS:
        if os.path.isfile(candidate):
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    return None


def png_copy(browser, svg_path, png_path, scale=2):
    """Draw one SVG at `scale` times its size, in its light colours."""
    with open(svg_path, encoding="utf-8") as handle:
        width, height = (float(v) for v in re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"',
                                                    handle.read()).groups())
    source = "file:///" + os.path.abspath(svg_path).replace(os.sep, "/")
    page = ('<!doctype html><meta charset="utf-8"><meta name="color-scheme" content="light">'
            "<style>html,body{margin:0;background:#fff}img{display:block}</style>"
            f'<img src="{source}" width="{width}" height="{height}">')
    folder = tempfile.mkdtemp(prefix="figures-png-")
    try:
        html = os.path.join(folder, "page.html")
        with open(html, "w", encoding="utf-8") as handle:
            handle.write(page)
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        f"--user-data-dir={os.path.join(folder, 'profile')}",
                        f"--force-device-scale-factor={scale}",
                        f"--window-size={math.ceil(width)},{math.ceil(height)}",
                        f"--screenshot={os.path.abspath(png_path)}",
                        "file:///" + html.replace(os.sep, "/")],
                       capture_output=True, timeout=120)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return os.path.exists(png_path)


def png_copies(folder):
    browser = find_browser()
    if not browser:
        print("--png: neither Microsoft Edge nor Google Chrome was found; no PNG written")
        return 1
    os.makedirs(folder, exist_ok=True)
    names = sorted(name for name in os.listdir(IMG) if name.endswith(".svg"))
    failed = [name for name in names
              if not png_copy(browser, os.path.join(IMG, name),
                              os.path.join(folder, name[:-4] + ".png"))]
    print(f"wrote {len(names) - len(failed)} PNG copies to {os.path.relpath(folder, ROOT)}"
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--collect", action="store_true",
                        help="refresh docs/data/project.json from the last bench run, the "
                             "mutation report and git before drawing")
    parser.add_argument("--mutation-log", default=os.path.join(ROOT, "build", "mutation.log"),
                        help="report of the last complete run of tools/mutation.py")
    parser.add_argument("--png", metavar="FOLDER",
                        help="also write a PNG copy of every SVG in docs/img/ to this folder "
                             "(needs Microsoft Edge or Google Chrome)")
    args = parser.parse_args(argv)
    data = collect(args.mutation_log) if args.collect else load()
    os.makedirs(IMG, exist_ok=True)
    figures = build(data)
    for name, svg in figures.items():
        svgplot.save(os.path.join(IMG, name), svg)
    print(f"wrote {len(figures)} figures to docs/img/")
    return png_copies(os.path.abspath(args.png)) if args.png else 0


if __name__ == "__main__":
    sys.exit(main())
