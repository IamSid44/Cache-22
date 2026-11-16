#!/usr/bin/env python3
"""One-command regression for the whole repository.

    python tools/regress.py                  # everything except bench and sweep
    python tools/regress.py --full           # ... with every kernel in every configuration
    python tools/regress.py rtl --seeds 1000 # one stage, more random programs
    python tools/regress.py rtl -c dm -k sort -v   # one configuration, one program
    python tools/regress.py rtl --core ss    # one core: inorder, ooo or ss
    python tools/regress.py bench --update-results
    python tools/regress.py sweep            # docs/SWEEPS.md and its charts

Stages
  unit    Python unit tests of the tools (tools/tests/)
  sw      every program assembles and passes on the golden model
  blocks  testbenches of the cache, cache hierarchy, predictor and prefetcher
  rtl     every core, program and configuration against the golden model
  bench   the kernels on the benchmark configurations (docs/RESULTS.md)
  sweep   the kernels over a grid of cache configurations (docs/SWEEPS.md)

Generated files go to build/. The exit status is non-zero if anything fails.
"""

import argparse
import concurrent.futures
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import cachesim      # noqa: E402
import rvasm         # noqa: E402
import rvsim         # noqa: E402
import sweep         # noqa: E402

BUILD = os.path.join(ROOT, "build")
LOGS = os.path.join(tempfile.gettempdir(), "riscv-processor-cache-logs")
# No single run takes more than a minute or two; one that takes this long has
# stopped advancing.
RUN_SECONDS = 1200
# The core testbench's option for each cache's event log.
LOG_ARGS = {"ic": "icache_log", "dc": "dcache_log", "l2": "l2cache_log"}

# "ooo" and "ss" are the same RTL: the out-of-order core at width 1 (scalar) and
# at width 2 (superscalar).
CORES = {
    "inorder": {"top": "inorder_core", "dirs": ["rtl/inorder"], "defines": [],
                "params": {}},
    "ooo":     {"top": "ooo_core", "dirs": ["rtl/ooo"], "defines": ["CORE_OOO"],
                "params": {"WIDTH": 1}},
    "ss":      {"top": "ooo_core", "dirs": ["rtl/ooo"], "defines": ["CORE_OOO"],
                "params": {"WIDTH": 2}},
}
# Parameters that only the out-of-order core has.
OOO_PARAMS = ("ROB_ENTRIES", "RS_ENTRIES", "LSQ_ENTRIES")

# Programs the RTL cannot run yet, with the reason.
RTL_SKIP = {}


ALL = ("inorder", "ooo", "ss")
OOO = ("ooo", "ss")


def config(label, latency=0, jitter=0, cores=ALL, **params):
    """A machine configuration: testbench parameters plus memory timing, and
    the cores it applies to."""
    return {"label": label, "latency": latency, "jitter": jitter, "params": params,
            "cores": cores}


def window(rob, rs, lsq):
    """Sizes of the out-of-order core's reorder buffer, reservation stations
    and load/store queue."""
    return dict(ROB_ENTRIES=rob, RS_ENTRIES=rs, LSQ_ENTRIES=lsq)


def caches(i_sets, i_ways, d_sets, d_ways):
    return dict(IC_ENABLE=1, IC_SETS=i_sets, IC_WAYS=i_ways,
                DC_ENABLE=1, DC_SETS=d_sets, DC_WAYS=d_ways)


def predictor(entries=64, counter_bits=2, tag_bits=0, garbage=False):
    """Branch predictor parameters. garbage=True mixes in nonsense
    predictions (BP_MODE 2), to exercise recovery."""
    return dict(BP_MODE=2 if garbage else 1, BP_ENTRIES=entries,
                BP_COUNTER_BITS=counter_bits, BP_TAG_BITS=tag_bits)


OFF, NEXT_LINE, STRIDE, RANDOM = 0, 1, 2, 3
LRU, FIFO, RAND, PLRU = 0, 1, 2, 3          # replacement policies
BACK, THROUGH = 0, 1                        # what a store that hits does
ALLOCATE, AROUND = 0, 1                     # what a store that misses does


def policy(repl=LRU, hit=BACK, miss=ALLOCATE, wbuf=0, merge=0, i_repl=None):
    """Replacement policy of both caches (i_repl if the instruction cache
    differs), and the data cache's write policy and write buffer."""
    return dict(IC_REPL=repl if i_repl is None else i_repl, DC_REPL=repl,
                DC_WRITE_HIT=hit, DC_WRITE_MISS=miss, DC_WBUF_ENTRIES=wbuf,
                DC_WBUF_MERGE=merge)


def prefetch(i=OFF, d=OFF, distance=4):
    """Prefetcher modes for the instruction and data cache."""
    return dict(IC_PF_MODE=i, DC_PF_MODE=d, PF_DISTANCE=distance)


def victim(i=0, d=0):
    """Entries of the victim buffers beside the instruction and data cache."""
    return dict(IC_VICTIM_ENTRIES=i, DC_VICTIM_ENTRIES=d)


def l2(sets, ways, latency=4, jitter=0, inclusive=False, repl=LRU, hit=BACK,
       miss=ALLOCATE, wbuf=0, merge=0):
    """A second-level cache below both first-level caches: its shape, the
    cycles it takes to answer a hit (plus a random 0..jitter), whether it
    is inclusive, and its policies."""
    return dict(L2_ENABLE=1, L2_SETS=sets, L2_WAYS=ways, L2_HIT_LATENCY=latency,
                L2_HIT_JITTER=jitter, L2_INCLUSION=int(inclusive), L2_REPL=repl,
                L2_WRITE_HIT=hit, L2_WRITE_MISS=miss, L2_WBUF_ENTRIES=wbuf,
                L2_WBUF_MERGE=merge)


# Lines are 32 bytes, so 32 lines is a 1 KiB cache.
CONFIGS = {
    # --- measured by the bench stage ---
    "ideal":   config("Ideal memory"),
    "nocache": config("No caches", latency=10),
    "dm":      config("1 KiB direct-mapped caches", latency=10, **caches(32, 1, 32, 1)),
    "2way":    config("D cache 2-way", latency=10, **caches(32, 1, 16, 2)),
    "bp1":     config("Last-time predictor", latency=10, **caches(32, 1, 16, 2),
                      **predictor(counter_bits=1)),
    "bp2":     config("2-bit predictor", latency=10, **caches(32, 1, 16, 2),
                      **predictor()),
    "pf-next":   config("Next-line prefetch", latency=10, **caches(32, 1, 16, 2),
                        **predictor(), **prefetch(NEXT_LINE, NEXT_LINE)),
    "pf-stride": config("Stride prefetch for data", latency=10, **caches(32, 1, 16, 2),
                        **predictor(), **prefetch(NEXT_LINE, STRIDE)),
    # The same machine as pf-stride with the out-of-order core.
    "ooo":       config("Out-of-order, 16-entry reorder buffer", latency=10, cores=("ooo",),
                        **caches(32, 1, 16, 2), **predictor(),
                        **prefetch(NEXT_LINE, STRIDE), **window(16, 8, 8)),
    "ooo-32":    config("Out-of-order, 32-entry reorder buffer", latency=10, cores=("ooo",),
                        **caches(32, 1, 16, 2), **predictor(),
                        **prefetch(NEXT_LINE, STRIDE), **window(32, 16, 16)),
    # The same machine as ooo-32, two instructions wide.
    "ss":        config("Superscalar: two wide, 32-entry reorder buffer", latency=10,
                        cores=("ss",), **caches(32, 1, 16, 2), **predictor(),
                        **prefetch(NEXT_LINE, STRIDE), **window(32, 16, 16)),
    # --- cache policies, measured by the bench stage (in-order core) ---
    "lru":    config("LRU, write-back, allocate", latency=10, cores=("inorder",),
                     **caches(32, 1, 8, 4), **predictor(), **policy()),
    "fifo":   config("FIFO replacement", latency=10, cores=("inorder",),
                     **caches(32, 1, 8, 4), **predictor(), **policy(FIFO, i_repl=LRU)),
    "random": config("Random replacement", latency=10, cores=("inorder",),
                     **caches(32, 1, 8, 4), **predictor(), **policy(RAND, i_repl=LRU)),
    "plru":   config("Pseudo-LRU replacement", latency=10, cores=("inorder",),
                     **caches(32, 1, 8, 4), **predictor(), **policy(PLRU, i_repl=LRU)),
    "wb-buf": config("Write-back, 4-entry write buffer", latency=10, cores=("inorder",),
                     **caches(32, 1, 8, 4), **predictor(), **policy(wbuf=4)),
    "wt":     config("Write-through, no buffer", latency=10, cores=("inorder",),
                     **caches(32, 1, 8, 4), **predictor(), **policy(hit=THROUGH)),
    "wt-buf": config("Write-through, 4-entry write buffer", latency=10, cores=("inorder",),
                     **caches(32, 1, 8, 4), **predictor(), **policy(hit=THROUGH, wbuf=4)),
    "wt-merge": config("Write-through, 4-entry merging buffer", latency=10,
                       cores=("inorder",), **caches(32, 1, 8, 4), **predictor(),
                       **policy(hit=THROUGH, wbuf=4, merge=1)),
    "wt-na":  config("Write-through, no-allocate, 4-entry buffer", latency=10,
                     cores=("inorder",), **caches(32, 1, 8, 4), **predictor(),
                     **policy(hit=THROUGH, miss=AROUND, wbuf=4)),
    "wt-na-merge": config("Write-through, no-allocate, 4-entry merging buffer", latency=10,
                          cores=("inorder",), **caches(32, 1, 8, 4), **predictor(),
                          **policy(hit=THROUGH, miss=AROUND, wbuf=4, merge=1)),
    "wb-na-merge": config("Write-back, no-allocate, 4-entry merging buffer", latency=10,
                          cores=("inorder",), **caches(32, 1, 8, 4), **predictor(),
                          **policy(miss=AROUND, wbuf=4, merge=1)),
    # --- the hierarchy, measured by the bench stage (in-order core) ---
    # `dm` with a victim buffer beside the data cache, to set against `2way`.
    "dm-vb":  config("1 KiB direct-mapped caches, 4-entry victim buffer for data",
                     latency=10, cores=("inorder",), **caches(32, 1, 32, 1), **victim(d=4)),
    # One machine with a memory five times slower: the caches and predictor of
    # `lru`, then an 8 KiB second-level cache that answers in 4 cycles.
    "h-l1":   config("Slow memory, first-level caches only", latency=50,
                     cores=("inorder",), **caches(32, 1, 8, 4), **predictor()),
    "h-l2":   config("8 KiB second-level cache", latency=50, cores=("inorder",),
                     **caches(32, 1, 8, 4), **predictor(), **l2(64, 4)),
    "h-incl": config("Inclusive second-level cache", latency=50, cores=("inorder",),
                     **caches(32, 1, 8, 4), **predictor(), **l2(64, 4, inclusive=True)),
    "h-vb":   config("Second-level cache, 4-entry victim buffer for data", latency=50,
                     cores=("inorder",), **caches(32, 1, 8, 4), **predictor(),
                     **l2(64, 4), **victim(d=4)),
    "h-pf":   config("Second-level cache, prefetchers", latency=50, cores=("inorder",),
                     **caches(32, 1, 8, 4), **predictor(), **l2(64, 4),
                     **prefetch(NEXT_LINE, STRIDE)),
    # --- verification only: odd shapes, random memory timing, bad predictions ---
    "ideal-bp":       config("Ideal memory, 2-bit predictor", **predictor()),
    "ideal-garbage":  config("Ideal memory, garbage predictions",
                             **predictor(entries=16, garbage=True)),
    "nocache-jitter": config("No caches, random latency, garbage predictions",
                             latency=1, jitter=3, **predictor(entries=8, garbage=True)),
    "one-line":       config("One-line caches, random latency, 4-entry predictor, "
                             "random data prefetches",
                             latency=1, jitter=2, **caches(1, 1, 1, 1),
                             **predictor(entries=4), **prefetch(NEXT_LINE, RANDOM)),
    "4way-tiny":      config("2 sets x 4 ways, random latency, aliasing predictor, "
                             "stride prefetch at distance 1",
                             latency=2, jitter=3, **caches(2, 4, 2, 4),
                             **predictor(entries=8, tag_bits=1),
                             **prefetch(NEXT_LINE, STRIDE, distance=1)),
    "icache-only":    config("I cache only, random latency, garbage predictions, "
                             "random instruction prefetches",
                             latency=1, jitter=2, IC_ENABLE=1, IC_SETS=4, IC_WAYS=2,
                             **predictor(entries=4, tag_bits=2, garbage=True),
                             **prefetch(RANDOM, OFF)),
    "dcache-only":    config("D cache only, random latency, 1-entry last-time predictor, "
                             "next-line data prefetch",
                             latency=1, jitter=2, DC_ENABLE=1, DC_SETS=4, DC_WAYS=2,
                             **predictor(entries=1, counter_bits=1),
                             **prefetch(OFF, NEXT_LINE)),
    "pf-garbage":     config("Small caches, random latency, garbage predictions, "
                             "random prefetches on both caches",
                             latency=1, jitter=3, **caches(4, 2, 4, 2),
                             **predictor(entries=8, garbage=True),
                             **prefetch(RANDOM, RANDOM)),
    # Cache policies under the same kind of pressure: tiny caches, random
    # latency, and prefetches or garbage predictions behind them.
    "p-fifo":    config("FIFO, 2 sets x 4 ways, random latency, garbage predictions, "
                        "random data prefetches",
                        latency=1, jitter=3, **caches(2, 4, 2, 4), **policy(FIFO),
                        **predictor(entries=8, garbage=True), **prefetch(NEXT_LINE, RANDOM)),
    "p-random":  config("Random replacement, 4 x 2 and 1 x 4, random latency, "
                        "stride prefetch",
                        latency=1, jitter=3, **caches(4, 2, 1, 4), **policy(RAND),
                        **predictor(entries=8), **prefetch(NEXT_LINE, STRIDE, distance=1)),
    "p-plru":    config("Pseudo-LRU, 2 sets x 4 ways, random latency, random prefetches "
                        "on both caches",
                        latency=2, jitter=2, **caches(2, 4, 2, 4), **policy(PLRU),
                        **predictor(entries=16), **prefetch(RANDOM, RANDOM)),
    "p-wt":      config("Write-through, no buffer, random latency, garbage predictions, "
                        "random data prefetches",
                        latency=1, jitter=2, **caches(4, 2, 4, 2), **policy(hit=THROUGH),
                        **predictor(entries=8, garbage=True), **prefetch(OFF, RANDOM)),
    "p-wt-buf1": config("Write-through, 1-entry buffer, 1 set x 2 ways, FIFO, random "
                        "data prefetches",
                        latency=1, jitter=3, **caches(4, 2, 1, 2),
                        **policy(FIFO, hit=THROUGH, wbuf=1),
                        **predictor(entries=4), **prefetch(NEXT_LINE, RANDOM)),
    "p-na":      config("Write-back, no-allocate, no buffer, random data prefetches",
                        latency=1, jitter=2, **caches(4, 2, 2, 2), **policy(miss=AROUND),
                        **predictor(entries=8, garbage=True), **prefetch(OFF, RANDOM)),
    "p-na-buf":  config("Write-through, no-allocate, 2-entry merging buffer, pseudo-LRU, "
                        "random prefetches",
                        latency=2, jitter=3, **caches(2, 2, 1, 4),
                        **policy(PLRU, hit=THROUGH, miss=AROUND, wbuf=2, merge=1),
                        **predictor(entries=8), **prefetch(RANDOM, RANDOM)),
    "p-wb-buf":  config("Write-back through a 1-entry buffer, one-line data cache, "
                        "random data prefetches, garbage predictions",
                        latency=1, jitter=3, **caches(4, 1, 1, 1), **policy(wbuf=1),
                        **predictor(entries=8, garbage=True), **prefetch(NEXT_LINE, RANDOM)),
    # A prefetcher that follows the program, so that stores meet lines that are
    # on their way.
    "p-na-next": config("Write-back, no-allocate, 2-entry buffer, next-line prefetch "
                        "on both caches",
                        latency=2, jitter=2, **caches(4, 2, 2, 2),
                        **policy(miss=AROUND, wbuf=2),
                        **predictor(entries=8), **prefetch(NEXT_LINE, NEXT_LINE)),
    # The hierarchy under the same kind of pressure.
    "l2-one":     config("One-line L2 under 8-line caches, random latency at both levels, "
                         "garbage predictions, random data prefetches",
                         latency=1, jitter=2, **caches(4, 2, 4, 2),
                         **l2(1, 1, latency=1, jitter=2),
                         **predictor(entries=8, garbage=True), **prefetch(NEXT_LINE, RANDOM)),
    "l2-incl":    config("Inclusive L2 of 4 lines under caches of 8, random L2 latency, "
                         "random prefetches on both caches",
                         latency=1, jitter=3, **caches(4, 2, 2, 4),
                         **l2(2, 2, latency=1, jitter=3, inclusive=True),
                         **predictor(entries=8), **prefetch(RANDOM, RANDOM)),
    "l2-wt-wb":   config("Write-through data cache with a 1-entry buffer over a write-back "
                         "FIFO L2, next-line data prefetch",
                         latency=2, jitter=2, **caches(4, 2, 4, 2),
                         **policy(hit=THROUGH, wbuf=1), **l2(4, 2, latency=1, repl=FIFO),
                         **predictor(entries=8, garbage=True), **prefetch(OFF, NEXT_LINE)),
    "l2-wb-wt":   config("Write-back data cache over a write-through, no-allocate L2 "
                         "with a 2-entry merging buffer, random prefetches",
                         latency=1, jitter=3, **caches(2, 2, 2, 2),
                         **l2(2, 4, latency=2, jitter=1, hit=THROUGH, miss=AROUND,
                              wbuf=2, merge=1),
                         **predictor(entries=8), **prefetch(RANDOM, RANDOM)),
    "l2-incl-wt": config("Write-through data cache without a buffer over an inclusive "
                         "pseudo-LRU L2 of 4 lines with a 1-entry buffer",
                         latency=1, jitter=2, **caches(4, 2, 4, 2), **policy(hit=THROUGH),
                         **l2(1, 4, latency=1, jitter=1, inclusive=True, repl=PLRU, wbuf=1),
                         **predictor(entries=8, garbage=True), **prefetch(NEXT_LINE, RANDOM)),
    "l2-only":    config("No first-level caches; a 4-line write-through, no-allocate "
                         "L2 with random replacement and a 1-entry buffer",
                         latency=1, jitter=2,
                         **l2(2, 2, latency=1, jitter=2, repl=RAND, hit=THROUGH,
                              miss=AROUND, wbuf=1),
                         **predictor(entries=8)),
    # Victim buffers.
    "vb-one":     config("One-line caches with 2-entry victim buffers, random latency, "
                         "garbage predictions, random data prefetches",
                         latency=1, jitter=3, **caches(1, 1, 1, 1), **victim(2, 2),
                         **predictor(entries=8, garbage=True), **prefetch(NEXT_LINE, RANDOM)),
    "vb-na":      config("Victim buffers beside a no-allocate, random-replacement data "
                         "cache with a 1-entry write buffer",
                         latency=2, jitter=2, **caches(4, 1, 2, 2), **victim(1, 3),
                         **policy(RAND, miss=AROUND, wbuf=1, i_repl=LRU),
                         **predictor(entries=8), **prefetch(OFF, RANDOM)),
    "vb-wt":      config("Victim buffers beside a write-through FIFO data cache with a "
                         "2-entry merging buffer, next-line prefetch on both",
                         latency=1, jitter=2, **caches(2, 2, 2, 2), **victim(3, 1),
                         **policy(FIFO, hit=THROUGH, wbuf=2, merge=1),
                         **predictor(entries=8, garbage=True),
                         **prefetch(NEXT_LINE, NEXT_LINE)),
    "vb-incl":    config("Victim buffers under an inclusive L2 smaller than the caches "
                         "and buffers above it, random L2 latency",
                         latency=1, jitter=2, **caches(2, 2, 2, 2), **victim(2, 3),
                         **policy(wbuf=1), **l2(2, 2, latency=1, jitter=2, inclusive=True),
                         **predictor(entries=8), **prefetch(NEXT_LINE, RANDOM)),
    # Out-of-order core only: the smallest windows, where every structure is
    # full almost all the time.
    "ooo-tiny":       config("2-entry reorder buffer, 1 reservation station, "
                             "2-entry load/store queue", cores=OOO,
                             **predictor(), **window(2, 1, 2)),
    "ooo-small":      config("4-entry reorder buffer, small caches, random latency, "
                             "garbage predictions and prefetches",
                             latency=1, jitter=3, cores=OOO, **caches(4, 2, 4, 2),
                             **predictor(entries=8, garbage=True),
                             **prefetch(RANDOM, RANDOM), **window(4, 2, 2)),
    # Two wide only: a window of exactly one pair, with garbage predictions.
    "ss-pair":        config("2-entry reorder buffer, 2 reservation stations, "
                             "2-entry load/store queue, garbage predictions", cores=("ss",),
                             **predictor(entries=16, garbage=True), **window(2, 2, 2)),
}
BENCH_CONFIGS = ["ideal", "nocache", "dm", "2way", "bp1", "bp2", "pf-next", "pf-stride",
                 "ooo", "ooo-32", "ss"]
POLICY_CONFIGS = ["lru", "fifo", "random", "plru", "wb-buf", "wt", "wt-buf", "wt-merge",
                  "wt-na", "wt-na-merge", "wb-na-merge"]
REPL_CONFIGS = ["lru", "fifo", "random", "plru"]
WRITE_CONFIGS = ["lru", "wb-buf", "wt", "wt-buf", "wt-merge", "wt-na", "wt-na-merge",
                 "wb-na-merge"]
# Kernels written for the cache policies.
POLICY_KERNELS = ("lru_fifo", "ways_plus_one", "fill", "store_hot")
# The hierarchy: the slow-memory machine with and without a second level.
HIER_CONFIGS = ["h-l1", "h-l2", "h-incl", "h-vb", "h-pf"]
VICTIM_CONFIGS = ["dm", "dm-vb", "2way"]
# Kernels written for the hierarchy, measured on its configurations only.
HIER_KERNELS = ("ws_walk", "incl_victim")
# The staircase: ws_walk at these working-set sizes (bytes), each run twice,
# with this many loads and with twice as many.
WS_SIZES = (256, 512, 1024, 2048, 4096, 8192, 16384, 32768)
WS_READS = 4096
STAIR_CONFIGS = ["h-l1", "h-l2", "h-incl"]
VERIFY_CONFIGS = ["ideal", "ideal-bp", "ideal-garbage", "nocache-jitter", "one-line",
                  "dm", "pf-stride", "4way-tiny", "icache-only", "dcache-only",
                  "pf-garbage", "p-fifo", "p-random", "p-plru", "p-wt", "p-wt-buf1",
                  "p-na", "p-na-buf", "p-wb-buf", "p-na-next",
                  "l2-one", "l2-incl", "l2-wt-wb", "l2-wb-wt", "l2-incl-wt", "l2-only",
                  "vb-one", "vb-na", "vb-wt", "vb-incl",
                  "ooo-32", "ss", "ooo-tiny", "ooo-small", "ss-pair"]


# Configurations in which the rtl stage runs every kernel: the machines the
# kernels are measured on.
KERNEL_CONFIGS = ("ideal", "dm", "pf-stride", "ooo-32", "ss")


def bench_core(config_name):
    """The core a benchmark configuration is measured on."""
    return CONFIGS[config_name]["cores"][0]

# Unit testbenches for individual blocks.
MEMORY_TIMINGS = [["+mem_latency=0"], ["+mem_latency=1"],
                  ["+mem_latency=3", "+mem_jitter=4", "+mem_seed=7"]]
CACHE_TB = ["rtl/common/cache.sv", "tb/sim_memory.sv", "tb/cache_tb.sv"]
HIER_TB = ["rtl/common/rv_pkg.sv", "rtl/common/cache.sv", "rtl/common/line_arbiter.sv",
           "rtl/common/prefetcher.sv", "rtl/common/mem_subsystem.sv", "tb/sim_memory.sv",
           "tb/hier_tb.sv"]
BPRED_TB = ["rtl/common/bpred.sv", "tb/bpred_tb.sv"]
PREFETCHER_TB = ["rtl/common/prefetcher.sv", "tb/prefetcher_tb.sv"]


def cache_test(name, sets, ways, repl=LRU, hit=BACK, miss=ALLOCATE, wbuf=0, merge=0,
               vb=0, inclusive=False, give_up=False):
    """One run of tb/cache_tb.sv. give_up: the testbench also asks the cache
    for lines back at random moments, as an inclusive cache below it
    would."""
    return (f"cache: {name}", CACHE_TB, "cache_tb",
            dict(SETS=sets, WAYS=ways, REPL=repl, WRITE_HIT=hit, WRITE_MISS=miss,
                 WBUF_ENTRIES=wbuf, WBUF_MERGE=merge, VICTIM_ENTRIES=vb,
                 INCLUSION=int(inclusive), GIVE_UP=int(give_up)), MEMORY_TIMINGS)


def hier_test(name, **params):
    """One run of tb/hier_tb.sv: two first-level caches of 4 sets x 2 ways
    over an L2 of 8 sets x 2 ways, unless the parameters say otherwise."""
    full = dict(IC_ENABLE=1, IC_SETS=4, IC_WAYS=2, DC_ENABLE=1, DC_SETS=4, DC_WAYS=2,
                L2_ENABLE=1, L2_SETS=8, L2_WAYS=2, L2_HIT_LATENCY=2, OPS=8000)
    full.update(params)
    return (f"hierarchy: {name}", HIER_TB, "hier_tb", full, MEMORY_TIMINGS)


BLOCK_TESTS = [
    ("cache: disabled",         CACHE_TB, "cache_tb", dict(ENABLE=0), MEMORY_TIMINGS),
    cache_test("1 line", 1, 1),
    cache_test("8 sets x 1 way", 8, 1),
    cache_test("4 sets x 2 ways", 4, 2),
    cache_test("1 set x 4 ways", 1, 4),
    cache_test("2 sets x 3 ways", 2, 3),
    cache_test("FIFO, 4 sets x 2 ways", 4, 2, FIFO),
    cache_test("FIFO, 2 sets x 3 ways", 2, 3, FIFO),
    cache_test("random, 4 sets x 2 ways", 4, 2, RAND),
    cache_test("random, 1 set x 4 ways", 1, 4, RAND),
    cache_test("pseudo-LRU, 1 set x 4 ways", 1, 4, PLRU),
    cache_test("pseudo-LRU, 2 sets x 8 ways", 2, 8, PLRU),
    cache_test("write-through, 4 sets x 2 ways", 4, 2, hit=THROUGH),
    cache_test("write-through, 1 line", 1, 1, hit=THROUGH),
    cache_test("write-through, 2-entry buffer", 4, 2, hit=THROUGH, wbuf=2),
    cache_test("write-through, merging buffer, pseudo-LRU", 2, 4, PLRU, hit=THROUGH,
               wbuf=2, merge=1),
    cache_test("no-allocate, 4 sets x 2 ways", 4, 2, miss=AROUND),
    cache_test("no-allocate, 1-entry buffer", 8, 1, miss=AROUND, wbuf=1),
    cache_test("no-allocate, merging buffer, random", 1, 4, RAND, miss=AROUND,
               wbuf=4, merge=1),
    cache_test("write-through, no-allocate", 4, 2, hit=THROUGH, miss=AROUND),
    cache_test("write-through, no-allocate, merging buffer, FIFO", 2, 3, FIFO,
               hit=THROUGH, miss=AROUND, wbuf=3, merge=1),
    cache_test("write-back through a 1-entry buffer, 1 line", 1, 1, wbuf=1),
    cache_test("write-back through a 4-entry buffer", 4, 2, wbuf=4),
    # Lines asked back by a cache below, and what an inclusive cache does itself.
    cache_test("lines given up, 4 sets x 2 ways", 4, 2, give_up=True),
    cache_test("lines given up, write-through, no buffer", 2, 3, FIFO, hit=THROUGH,
               give_up=True),
    cache_test("lines given up, no-allocate, merging buffer, random", 1, 4, RAND,
               miss=AROUND, wbuf=2, merge=1, give_up=True),
    cache_test("inclusive, 4 sets x 2 ways, 2-entry buffer", 4, 2, wbuf=2, inclusive=True),
    cache_test("inclusive, 1 line, lines given up", 1, 1, inclusive=True, give_up=True),
    # Victim buffer.
    cache_test("victim buffer, 8 sets x 1 way, 2 entries", 8, 1, vb=2),
    cache_test("victim buffer, 1 line, 1 entry", 1, 1, vb=1),
    cache_test("victim buffer, FIFO, 2 sets x 3 ways, 1-entry write buffer", 2, 3, FIFO,
               wbuf=1, vb=3),
    cache_test("victim buffer, no-allocate, random, lines given up", 4, 2, RAND,
               miss=AROUND, vb=2, give_up=True),
    cache_test("victim buffer, write-through, no-allocate, merging buffer, pseudo-LRU, "
               "lines given up", 2, 4, PLRU, hit=THROUGH, miss=AROUND, wbuf=2, merge=1,
               vb=4, give_up=True),
    # The hierarchy: both first-level caches, the arbiter and the L2.
    hier_test("non-inclusive"),
    hier_test("inclusive", L2_INCLUSION=1),
    hier_test("inclusive L2 smaller than the caches above, random hit latency",
              L2_INCLUSION=1, L2_SETS=2, L2_WAYS=2, L2_HIT_LATENCY=1, L2_HIT_JITTER=3,
              IC_PF_MODE=1, DC_PF_MODE=3),
    hier_test("one-line L2, prefetchers", L2_SETS=1, L2_WAYS=1, L2_HIT_LATENCY=1,
              IC_PF_MODE=1, DC_PF_MODE=1),
    hier_test("write-through over write-back, FIFO L2", DC_WRITE_HIT=1, DC_WBUF_ENTRIES=2,
              L2_REPL=1, L2_HIT_JITTER=2),
    hier_test("write-through without a buffer over an inclusive L2", DC_WRITE_HIT=1,
              L2_INCLUSION=1, L2_SETS=1, L2_WAYS=4, L2_REPL=3),
    hier_test("write-back over write-through, no-allocate, merging buffer", L2_WRITE_HIT=1,
              L2_WRITE_MISS=1, L2_WBUF_ENTRIES=2, L2_WBUF_MERGE=1, L2_SETS=4, L2_WAYS=2),
    hier_test("no-allocate with merging buffer over an inclusive L2 with a buffer",
              DC_WRITE_MISS=1, DC_WBUF_ENTRIES=2, DC_WBUF_MERGE=1, DC_REPL=2,
              L2_INCLUSION=1, L2_SETS=4, L2_WAYS=2, L2_WBUF_ENTRIES=1, L2_HIT_JITTER=2,
              DC_PF_MODE=1),
    hier_test("victim buffers, non-inclusive", IC_VICTIM_ENTRIES=2, DC_VICTIM_ENTRIES=3,
              IC_SETS=8, IC_WAYS=1, DC_SETS=8, DC_WAYS=1),
    hier_test("victim buffers under an inclusive L2 smaller than they are",
              IC_VICTIM_ENTRIES=2, DC_VICTIM_ENTRIES=3, DC_WBUF_ENTRIES=1, L2_INCLUSION=1,
              L2_SETS=2, L2_WAYS=2, L2_HIT_LATENCY=1, DC_PF_MODE=3),
    hier_test("write-through, no-allocate at both levels", DC_WRITE_HIT=1, DC_WRITE_MISS=1,
              DC_WBUF_ENTRIES=1, L2_WRITE_HIT=1, L2_WRITE_MISS=1, L2_WBUF_ENTRIES=2,
              L2_WBUF_MERGE=1, L2_SETS=4, L2_WAYS=2),
    hier_test("no first-level caches", IC_ENABLE=0, DC_ENABLE=0, L2_HIT_LATENCY=1,
              L2_HIT_JITTER=2, L2_REPL=2),
    hier_test("no L2", L2_ENABLE=0, DC_VICTIM_ENTRIES=2),
    ("predictor: 2-bit",            BPRED_TB, "bpred_tb", dict(COUNTER_BITS=2), [[]]),
    ("predictor: last-time",        BPRED_TB, "bpred_tb", dict(COUNTER_BITS=1), [[]]),
    ("predictor: 2 entries",        BPRED_TB, "bpred_tb", dict(ENTRIES=2), [[]]),
    ("predictor: 1-bit tags",       BPRED_TB, "bpred_tb", dict(TAG_BITS=1), [[]]),
    ("prefetcher: off",             PREFETCHER_TB, "prefetcher_tb", dict(MODE=0), [[]]),
    ("prefetcher: next line",       PREFETCHER_TB, "prefetcher_tb", dict(MODE=1), [[]]),
    ("prefetcher: stride",          PREFETCHER_TB, "prefetcher_tb", dict(MODE=2), [[]]),
    ("prefetcher: stride, distance 1", PREFETCHER_TB, "prefetcher_tb",
     dict(MODE=2, DISTANCE=1, ENTRIES=16), [[]]),
]


def rel(path):
    return os.path.relpath(path, ROOT).replace("\\", "/")


def have_icarus():
    return bool(shutil.which("iverilog") and shutil.which("vvp"))


def pool_map(function, items):
    with concurrent.futures.ThreadPoolExecutor(os.cpu_count() or 4) as pool:
        return list(pool.map(function, items))


# --------------------------------------------------------------------------
# Golden model
# --------------------------------------------------------------------------
def golden_run(source_path, stem, max_instructions):
    """Assemble and run on the golden model, writing <stem>.hex/.lst/.trace."""
    os.makedirs(os.path.dirname(stem), exist_ok=True)
    try:
        image, listing, _ = rvasm.assemble_file(source_path)
    except rvasm.AsmError as err:
        return False, f"assembly error: {err}", None
    rvasm.write_hex(image, stem + ".hex")
    with open(stem + ".lst", "w", encoding="utf-8") as handle:
        handle.write(rvasm.format_listing(listing))
    with open(stem + ".trace", "w", encoding="ascii") as trace:
        sim = rvsim.Simulator(image, trace=trace)
        outcome = sim.run(max_instructions)
    sim.mem_hash = rvsim.memory_hash(sim.mem)
    if outcome == "timeout":
        return False, f"golden model timeout at pc={sim.pc:#x}", sim
    if sim.exit_code != 0:
        return False, f"golden model exit code {sim.exit_code}", sim
    return True, f"{sim.retired} instr, {sim.trap_count} traps", sim


def program_sources(args, folders):
    """(label, path) for every .S file in the given sw/ sub-folders."""
    found = []
    for folder in folders:
        directory = os.path.join(ROOT, "sw", folder)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            label = f"{folder}/{name}"
            if name.endswith(".S") and not (args.k and args.k not in label):
                found.append((label, os.path.join(directory, name)))
    return found


def prepare_goldens(programs, args):
    """Run the golden model once per program."""
    goldens = {}
    for label, path in programs:
        stem = os.path.join(BUILD, "golden", label[:-2] if label.endswith(".S") else label)
        ok, detail, sim = golden_run(path, stem, args.max)
        goldens[label] = (ok, detail, sim, stem)
    return goldens


# --------------------------------------------------------------------------
# RTL simulation
# --------------------------------------------------------------------------
def rtl_sources(core):
    sources = ["rtl/common/rv_pkg.sv"]
    for directory in ["rtl/common"] + CORES[core]["dirs"]:
        for name in sorted(os.listdir(os.path.join(ROOT, directory))):
            path = f"{directory}/{name}"
            if name.endswith(".sv") and path not in sources:
                sources.append(path)
    return sources


def build_core(core, config_name):
    """Compile a core in one configuration."""
    params = {key: value for key, value in CONFIGS[config_name]["params"].items()
              if core in OOO or key not in OOO_PARAMS}
    params.update(CORES[core]["params"])
    out = os.path.join(BUILD, "rtl", f"{core}-{config_name}.vvp")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    command = (["iverilog", "-g2012", f"-DCORE={CORES[core]['top']}", "-o", rel(out)]
               + [f"-D{name}" for name in CORES[core]["defines"]]
               + [f"-Pcore_tb.{key}={value}" for key, value in params.items()]
               + rtl_sources(core) + ["tb/sim_memory.sv", "tb/core_tb.sv"])
    proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    if proc.returncode:
        raise RuntimeError("compile error:\n" + (proc.stderr or proc.stdout))
    return out


def run_rtl(vvp_file, hex_file, trace_file, plusargs=(), max_cycles=5_000_000, max_events=0):
    """Run one program."""
    if os.path.exists(trace_file):
        os.remove(trace_file)
    # The cycle limit ends a run in which the core hangs.
    try:
        proc = subprocess.run(
            ["vvp", "-n", rel(vvp_file), f"+hex={rel(hex_file)}",
             f"+trace={rel(trace_file)}", f"+max_cycles={max_cycles}",
             f"+max_events={max_events}", *plusargs],
            cwd=ROOT, capture_output=True, text=True, errors="replace", timeout=RUN_SECONDS)
    except subprocess.TimeoutExpired:
        return {"status": "stuck", "console": "",
                "error": f"the simulation did not finish in {RUN_SECONDS} seconds "
                         "(a combinational loop?)"}
    stats = {"status": "crash", "console": proc.stdout}
    for line in proc.stdout.splitlines():
        if line.startswith(("STATS ", "STATS2 ")):
            for field in line.split()[1:]:
                key, _, value = field.partition("=")
                stats[key] = int(value) if value.isdigit() else value
        elif line.startswith("FINAL "):
            # Taken one step after the last clock edge: the memory checksum and
            # the cache counters including that edge.
            stats["final"] = {key: int(value) if value.isdigit() else value
                              for key, _, value in
                              (field.partition("=") for field in line.split()[1:])}
        elif line.startswith("ERROR"):
            stats["error"] = line
    return stats


def compare_traces(golden_file, rtl_file):
    """None if identical, otherwise a description of the first difference."""
    with open(golden_file, encoding="ascii") as handle:
        golden = handle.read().splitlines()
    try:
        with open(rtl_file, encoding="ascii") as handle:
            actual = handle.read().splitlines()
    except FileNotFoundError:
        return "RTL produced no trace"
    for number, (want, got) in enumerate(zip(golden, actual), 1):
        if want != got:
            return f"trace line {number}: golden [{want}] rtl [{got}]"
    if len(golden) != len(actual):
        return (f"trace length: golden {len(golden)} lines, rtl {len(actual)} "
                f"lines (identical up to line {min(len(golden), len(actual))})")
    return None


def cache_models(params):
    """The caches a configuration has, as {"ic"/"dc"/"l2": cachesim
    parameters}."""
    models = {}
    for side, default_shape in (("IC", (32, 1)), ("DC", (32, 1)), ("L2", (64, 4))):
        if not params.get(f"{side}_ENABLE"):
            continue
        model = dict(sets=params.get(f"{side}_SETS", default_shape[0]),
                     ways=params.get(f"{side}_WAYS", default_shape[1]),
                     line_bytes=params.get("LINE_BYTES", 32),
                     repl=params.get(f"{side}_REPL", 0))
        if side != "IC":
            model.update(write_hit=params.get(f"{side}_WRITE_HIT", 0),
                         write_miss=params.get(f"{side}_WRITE_MISS", 0),
                         wbuf_entries=params.get(f"{side}_WBUF_ENTRIES", 0),
                         wbuf_merge=params.get(f"{side}_WBUF_MERGE", 0))
        if side == "L2":
            model.update(inclusion=params.get("L2_INCLUSION", 0))
        else:
            model.update(victim_entries=params.get(f"{side}_VICTIM_ENTRIES", 0))
        models[side.lower()] = model
    return models


def check_cache_log(side, log_file, kept, model, final, keep=False):
    """Replay one cache's event log against the model."""
    try:
        error, counters = cachesim.check_log_file(log_file, **model)
    except FileNotFoundError:
        return f"{side}: the cache wrote no event log"
    if not error:
        for name, value in counters.items():
            got = final.get(f"{side}_{name}")
            if got is not None and got != value:
                error = (f"{side} counter {name}: the cache reports {got}, its event "
                         f"log amounts to {value}")
                break
    else:
        error = f"{side} {error}"
    if not error:
        if not keep:
            os.remove(log_file)
        return None
    shutil.move(log_file, kept)             # keep the evidence, in build/
    return f"{error} (log: {rel(kept)})"


def memory_difference(vvp_file, hex_file, plusargs, max_cycles, sim, stem):
    """Run again with a memory dump, to say where the final image differs."""
    dump = stem + ".memdump"
    subprocess.run(["vvp", "-n", rel(vvp_file), f"+hex={rel(hex_file)}",
                    f"+max_cycles={max_cycles}", f"+memdump={rel(dump)}", *plusargs],
                   cwd=ROOT, capture_output=True, text=True, errors="replace")
    try:
        with open(dump, encoding="ascii") as handle:
            image = [int(token, 16) for token in handle.read().split()
                     if not token.startswith(("//", "@"))]
    except (FileNotFoundError, ValueError):
        return "final memory differs from the golden model's"
    for address, (want, got) in enumerate(zip(sim.mem, image)):
        if want != got:
            return (f"final memory differs from the golden model's: byte {address:#x} is "
                    f"{got:#04x}, expected {want:#04x} (dump: {rel(dump)})")
    return "final memory differs from the golden model's"


def check_on_rtl(vvp_file, core, config_name, label, golden, cache_check=True,
                 keep_logs=()):
    """RTL run of one program in one configuration."""
    ok, detail, sim, stem = golden
    if not ok:
        return False, detail, {}
    cfg = CONFIGS[config_name]
    params = cfg["params"]
    transfers = 6 + params.get("DC_WBUF_ENTRIES", 0)
    per_instr = 16 + transfers * (cfg["latency"] + cfg["jitter"])
    if params.get("L2_ENABLE"):
        per_instr = 16 + (transfers + params.get("L2_WBUF_ENTRIES", 0)) * (
            4 * (cfg["latency"] + cfg["jitter"])
            + 2 * (params.get("L2_HIT_LATENCY", 4) + params.get("L2_HIT_JITTER", 0)) + 8)
    max_cycles = per_instr * (sim.retired + sim.trap_count) + 5_000
    plusargs = [f"+mem_latency={cfg['latency']}", f"+mem_jitter={cfg['jitter']}",
                f"+mem_seed={zlib.crc32(label.encode()) & 0xFFFF}"]
    run_stem = os.path.join(BUILD, "rtl", core, config_name,
                            label[:-2] if label.endswith(".S") else label)
    trace = run_stem + ".trace"
    os.makedirs(os.path.dirname(trace), exist_ok=True)
    models = cache_models(cfg["params"]) if cache_check else {}
    os.makedirs(LOGS, exist_ok=True)
    flat = f"{core}-{config_name}-" + os.path.basename(run_stem) + f"-{zlib.crc32(label.encode()):08x}"
    logs = {side: os.path.join(LOGS, f"{flat}.{side}.log") for side in models}
    timing = list(plusargs)
    plusargs += [f"+{LOG_ARGS[side]}={path}" for side, path in logs.items()]
    # The golden trace has one line per instruction or trap.
    stats = run_rtl(vvp_file, stem + ".hex", trace, plusargs, max_cycles,
                    max_events=sim.retired + sim.trap_count)
    if stats["status"] == "runaway":
        difference = compare_traces(stem + ".trace", trace)
        return False, difference or "RTL reported more events than the golden trace", stats
    if stats["status"] != "halt":
        return False, stats.get("error", f"RTL run ended with {stats['status']}"), stats
    if "error" in stats:                    # an invariant checked inside the RTL
        return False, stats["error"], stats
    difference = compare_traces(stem + ".trace", trace)
    if difference:
        return False, difference + f" (traces: {rel(stem)}.trace, {rel(trace)})", stats
    if stats["exit"] != 0:
        return False, f"RTL exit code {stats['exit']}", stats
    final = stats.get("final", {})
    if final.get("memhash") != sim.mem_hash:
        return False, memory_difference(vvp_file, stem + ".hex", timing, max_cycles,
                                        sim, run_stem), stats
    for side, model in models.items():
        difference = check_cache_log(side, logs[side], f"{run_stem}.{side}.log", model, final,
                                     keep=side in keep_logs)
        if difference:
            return False, difference, stats
    stats["logs"] = {side: logs[side] for side in keep_logs if side in logs}
    ipc = stats["instret"] / stats["cycles"]
    return True, f"{stats['instret']} instr, {stats['cycles']} cycles, IPC {ipc:.2f}", stats


# --------------------------------------------------------------------------
# Stages
# --------------------------------------------------------------------------
def stage_unit(_args):
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tools/tests"],
        cwd=ROOT, capture_output=True, text=True)
    summary = proc.stderr.strip().splitlines()
    detail = summary[-1] if summary else ""
    counts = next((line for line in summary if line.startswith("Ran ")), "")
    if proc.returncode:
        sys.stdout.write(proc.stderr)
    return [("unit tests", proc.returncode == 0, f"{counts} {detail}".strip())]


def stage_sw(args):
    results = []
    for label, path in program_sources(args, ["tests", "bench"]):
        stem = os.path.join(BUILD, "sw", label[:-2])
        ok, detail, _ = golden_run(path, stem, args.max)
        results.append((label, ok, detail))
    return results


def stage_blocks(args):
    """Unit testbenches for RTL blocks: cache, branch predictor, prefetcher."""
    if not have_icarus():
        return [("blocks", False, "iverilog/vvp not found on PATH")]
    out_dir = os.path.join(BUILD, "blocks")
    os.makedirs(out_dir, exist_ok=True)

    def run(item):
        index, (name, sources, top, params, runs) = item
        vvp_file = os.path.join(out_dir, f"{top}_{index}.vvp")
        command = (["iverilog", "-g2012", "-o", rel(vvp_file)]
                   + [f"-P{top}.{key}={value}" for key, value in params.items()]
                   + sources)
        proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        if proc.returncode:
            return name, False, "compile error: " + (proc.stderr or proc.stdout).strip()[:300]
        verdict = "no verdict"
        # Every cache also writes its event log, which the model must agree
        # with.
        modelled = []
        if top == "cache_tb" and params.get("ENABLE", 1):
            modelled = [("log", "COUNT ", dict(
                sets=params["SETS"], ways=params["WAYS"], repl=params["REPL"],
                write_hit=params["WRITE_HIT"], write_miss=params["WRITE_MISS"],
                wbuf_entries=params["WBUF_ENTRIES"], wbuf_merge=params["WBUF_MERGE"],
                victim_entries=params.get("VICTIM_ENTRIES", 0),
                inclusion=params.get("INCLUSION", 0)))]
        elif top == "hier_tb":
            modelled = [(f"{side}_log", f"COUNT_{side.upper()} ", model)
                        for side, model in cache_models(params).items()]
        os.makedirs(LOGS, exist_ok=True)
        logs = {option: os.path.join(LOGS, f"{top}_{index}.{option}")
                for option, _, _ in modelled}
        for plusargs in runs:
            extra = [f"+{option}={path}" for option, path in logs.items()]
            out = subprocess.run(["vvp", "-n", rel(vvp_file), f"+seed={index + 1}",
                                  *plusargs, *extra],
                                 cwd=ROOT, capture_output=True, text=True).stdout
            lines = out.splitlines()
            verdict = next((line for line in lines if line.startswith(("PASS", "FAIL"))),
                           "no verdict")
            if not verdict.startswith("PASS"):
                reason = next((line for line in lines
                               if not line.startswith(("PASS", "FAIL", "COUNT"))), "")
                return name, False, f"{' '.join(plusargs)} {verdict} {reason}".strip()
            for option, prefix, model in modelled:
                error, counters = cachesim.check_log_file(logs[option], must_drain=True,
                                                          **model)
                reported = dict(re.findall(r"(\w+)=(\d+)", next(
                    (line for line in lines if line.startswith(prefix)), "")))
                # The hierarchy testbench reports the counters each cache has;
                # the cache testbench reports every one.
                wrong = [f"{key} {reported.get(key)} (model {value})"
                         for key, value in counters.items()
                         if str(value) != reported.get(key, None if top == "cache_tb"
                                                       else str(value))]
                if error or wrong or not reported:
                    kept = os.path.join(out_dir, f"{top}_{index}.{option}")
                    shutil.move(logs[option], kept)
                    return name, False, (f"{' '.join(plusargs)} {option}: "
                                         + (error or "counters: " + ", ".join(wrong))
                                         + f" (log: {rel(kept)})")
                os.remove(logs[option])
        return name, True, verdict[5:]

    items = [item for item in enumerate(BLOCK_TESTS)
             if not (args.k and args.k not in item[1][0])]
    return pool_map(run, items)


def stage_rtl(args):
    if not have_icarus():
        return [("rtl", False, "iverilog/vvp not found on PATH")]
    results = []
    wanted = args.c.split(",") if args.c else VERIFY_CONFIGS
    for core in ([args.core] if args.core else list(CORES)):
        configs = [name for name in wanted if core in CONFIGS[name]["cores"]]
        if not configs:
            continue
        try:
            vvp = dict(zip(configs, pool_map(lambda name: build_core(core, name), configs)))
        except RuntimeError as err:
            results.append((f"{core}: build", False, str(err)))
            continue

        # Directed programs and benchmark kernels run in every configuration.
        programs = []
        for label, path in program_sources(args, ["tests", "bench"]):
            if os.path.basename(path) in RTL_SKIP:
                results.append((f"{core}: {label}", None, RTL_SKIP[os.path.basename(path)]))
            else:
                programs.append((label, path))

        # Random programs are spread over the configurations.
        seeds = []
        if args.seeds and (not args.k or args.k == "seed"):
            import rvgen
            rand_dir = os.path.join(BUILD, "rand")
            os.makedirs(rand_dir, exist_ok=True)
            for seed in range(args.seed_base, args.seed_base + args.seeds):
                path = os.path.join(rand_dir, f"seed_{seed}.S")
                with open(path, "w", encoding="ascii") as handle:
                    handle.write(rvgen.generate(seed, traps=bool(seed % 2)))
                seeds.append((f"rand/seed_{seed}", path, configs[(seed // 2) % len(configs)]))

        goldens = prepare_goldens(programs + [(label, path) for label, path, _ in seeds], args)

        # The kernels are long, and there are many configurations.
        def wanted_here(name, label):
            if not label.startswith("bench/") or args.full or args.k:
                return True
            return name in KERNEL_CONFIGS or zlib.crc32(f"{name}/{label}".encode()) % 10 == 0

        jobs = [(name, label) for name in configs for label, _ in programs
                if wanted_here(name, label)]
        jobs += [(name, label) for label, _, name in seeds]
        outcomes = pool_map(lambda job: check_on_rtl(vvp[job[0]], core, job[0], job[1],
                                                     goldens[job[1]],
                                                     not args.no_cache_check), jobs)

        for name in configs:
            mine = [(label, outcome) for (cfg_name, label), outcome in zip(jobs, outcomes)
                    if cfg_name == name]
            directed = [(l, o) for l, o in mine if not l.startswith("rand/")]
            randoms = [(l, o) for l, o in mine if l.startswith("rand/")]
            title = f"{core}/{name}"
            for group, what in ((directed, "programs"), (randoms, "random programs")):
                if not group:
                    continue
                failed = [(l, o) for l, o in group if not o[0]]
                if args.v:
                    for label, (ok, detail, _) in group:
                        results.append((f"{title}: {label}", ok, detail))
                    continue
                instr = sum(o[2].get("instret", 0) for _, o in group)
                traps = sum(o[2].get("traps", 0) for _, o in group)
                cycles = sum(o[2].get("cycles", 0) for _, o in group)
                summary = (f"{instr} instr, {traps} traps, {cycles} cycles" if not failed
                           else f"{len(failed)} of {len(group)} failed")
                results.append((f"{title}: {len(group)} {what}", not failed, summary))
                for label, (_, detail, _) in failed[:8]:
                    results.append((f"{title}: {label}", False, detail))
    return results


def staircase_programs():
    """ws_walk at every size and both run lengths: (label, path, size,
    reads)."""
    with open(os.path.join(ROOT, "sw", "bench", "ws_walk.S"), encoding="ascii") as handle:
        source = handle.read()
    folder = os.path.join(BUILD, "ws")
    os.makedirs(folder, exist_ok=True)
    programs = []
    for size in WS_SIZES:
        for reads in (WS_READS, 2 * WS_READS):
            text, first = re.subn(r"(?m)^\.equ WS_BYTES, \d+$", f".equ WS_BYTES, {size}", source)
            text, second = re.subn(r"(?m)^\.equ READS, \d+$", f".equ READS, {reads}", text)
            if (first, second) != (1, 1):
                raise RuntimeError("sw/bench/ws_walk.S: the WS_BYTES and READS lines "
                                   "are not where the staircase expects them")
            path = os.path.join(folder, f"ws_walk_{size}_{reads}.S")
            with open(path, "w", encoding="ascii", newline="\n") as handle:
                handle.write(text)
            programs.append((f"ws/ws_walk_{size}_{reads}", path, size, reads))
    return programs


def bench_tables(kernels, table, stairs, args):
    """Write the results tables, and docs/RESULTS.md if asked to."""
    markdown = results_markdown(kernels, table, stairs)
    with open(os.path.join(BUILD, "results.md"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(markdown)
    print(markdown)
    return markdown


def stage_bench(args):
    stats_file = os.path.join(BUILD, "bench_stats.json")
    if args.tables_only:
        # The counters of the last complete run, without simulating anything.
        try:
            with open(stats_file, encoding="utf-8") as handle:
                saved = json.load(handle)
        except FileNotFoundError:
            return [("bench: tables", False, f"no {rel(stats_file)}; run the bench stage first")]
        table = {tuple(key.split("@")): stats for key, stats in saved["table"].items()}
        stairs = {(int(size), int(reads), name): stats
                  for (size, reads, name), stats in
                  ((key.replace("/", "@").split("@"), stats)
                   for key, stats in saved["staircase"].items())}
        markdown = bench_tables(saved["kernels"], table, stairs, args)
        if args.update_results:
            update_results_doc(markdown)
        return [("bench: tables rebuilt from the last run", True, rel(stats_file))]
    if not have_icarus():
        return [("bench", False, "iverilog/vvp not found on PATH")]
    all_kernels = program_sources(args, ["bench"])
    goldens = prepare_goldens(all_kernels, args)
    results, table = [], {}
    measured = BENCH_CONFIGS + POLICY_CONFIGS + ["dm-vb"] + HIER_CONFIGS
    try:
        built = dict(zip(measured,
                         pool_map(lambda n: build_core(bench_core(n), n), measured)))
    except RuntimeError as err:
        return [("bench: build", False, str(err))]

    def measured_on(name, kernel):
        """Each family of configurations keeps the kernels it was built on."""
        if name in HIER_CONFIGS:
            return True
        if name in POLICY_CONFIGS:
            return kernel not in HIER_KERNELS
        return kernel not in POLICY_KERNELS and kernel not in HIER_KERNELS

    for name in measured:
        vvp_file = built[name]
        core = bench_core(name)
        kernels = [kernel for kernel in all_kernels if measured_on(name, kernel[0][6:-2])]
        outcomes = pool_map(
            lambda kernel: check_on_rtl(vvp_file, core, name, kernel[0], goldens[kernel[0]]),
            kernels)
        for (label, _), (ok, detail, stats) in zip(kernels, outcomes):
            results.append((f"{name}: {label}", ok, detail))
            if ok:
                table[(label[6:-2], name)] = stats

    # The staircase: ws_walk at each working-set size, two run lengths.
    stairs = {}
    if not args.k or "ws_walk" in args.k:
        programs = staircase_programs()
        stair_goldens = prepare_goldens([(label, path) for label, path, _, _ in programs], args)
        jobs = [(name, program) for name in STAIR_CONFIGS for program in programs]
        outcomes = pool_map(
            lambda job: check_on_rtl(built[job[0]], bench_core(job[0]), job[0], job[1][0],
                                     stair_goldens[job[1][0]]), jobs)
        for (name, (label, _, size, reads)), (ok, detail, stats) in zip(jobs, outcomes):
            results.append((f"{name}: {label}", ok, detail))
            if ok:
                stairs[(size, reads, name)] = stats

    kernel_names = [label[6:-2] for label, _ in all_kernels]

    def counters(stats):
        return {key: value for key, value in stats.items() if key != "console"}

    with open(stats_file, "w", encoding="utf-8") as handle:
        json.dump({"kernels": kernel_names,
                   "table": {f"{kernel}@{name}": counters(stats)
                             for (kernel, name), stats in table.items()},
                   "staircase": {f"{size}/{reads}@{name}": counters(stats)
                                 for (size, reads, name), stats in stairs.items()}},
                  handle, indent=1)
    markdown = bench_tables(kernel_names, table, stairs, args)
    if args.update_results and all(ok is not False for _, ok, _ in results):
        update_results_doc(markdown)
    return results


def results_markdown(kernels, table, stairs=None):
    """The generated part of docs/RESULTS.md."""
    def cell(kernel, name, function):
        stats = table.get((kernel, name))
        return function(stats) if stats else "-"

    def percent(part, whole):
        return f"{100 * part / whole:.0f}%" if whole else "-"

    def rate(part, whole):
        return f"{100 * part / whole:.1f}%" if whole else "-"

    all_kernels = kernels
    kernels = [kernel for kernel in kernels
               if kernel not in POLICY_KERNELS and kernel not in HIER_KERNELS]

    lines = ["### Configurations", "",
             "| Name | Core | Memory latency (cycles per line) | Instruction cache "
             "| Data cache | Branch predictor | Prefetch (instructions / data) |",
             "| --- | --- | ---: | --- | --- | --- | --- |"]
    pf_names = {0: "none", 1: "next line", 2: "stride", 3: "random"}
    for name in BENCH_CONFIGS:
        cfg = CONFIGS[name]
        params = cfg["params"]

        def shape(prefix):
            if not params.get(prefix + "_ENABLE"):
                return "none"
            sets, ways = params.get(prefix + "_SETS", 32), params.get(prefix + "_WAYS", 1)
            kind = "direct-mapped" if ways == 1 else f"{ways}-way"
            return f"{sets * ways * 32} bytes, {kind}"
        if params.get("BP_MODE"):
            bits = params.get("BP_COUNTER_BITS", 2)
            bp = (f"{params.get('BP_ENTRIES', 64)}-entry BTB, "
                  + ("2-bit counters" if bits == 2 else "last outcome"))
        else:
            bp = "none (always not taken)"
        pf = (f"{pf_names[params.get('IC_PF_MODE', 0)]} / "
              f"{pf_names[params.get('DC_PF_MODE', 0)]}")
        if bench_core(name) in OOO:
            width = CORES[bench_core(name)]["params"]["WIDTH"]
            core_text = (f"out-of-order, {width} wide: ROB {params['ROB_ENTRIES']}, "
                         f"{params['RS_ENTRIES']} stations, LSQ {params['LSQ_ENTRIES']}")
        else:
            core_text = "in-order"
        lines.append(f"| `{name}`: {cfg['label']} | {core_text} | {cfg['latency']} "
                     f"| {shape('IC')} | {shape('DC')} | {bp} | {pf} |")

    header = "| Kernel | Instructions | " + " | ".join(f"`{n}`" for n in BENCH_CONFIGS) + " |"
    rule = "| --- | ---: | " + " | ".join("---:" for _ in BENCH_CONFIGS) + " |"

    def instructions(kernel):
        return next((table[(kernel, n)]["instret"] for n in BENCH_CONFIGS
                     if (kernel, n) in table), "")

    lines += ["", "### Cycles", "", header, rule]
    for kernel in kernels:
        lines.append(f"| `{kernel}` | {instructions(kernel)} | " + " | ".join(
            cell(kernel, n, lambda s: str(s["cycles"])) for n in BENCH_CONFIGS) + " |")

    lines += ["", "### Instructions per cycle", "", header, rule]
    for kernel in kernels:
        lines.append(f"| `{kernel}` | {instructions(kernel)} | " + " | ".join(
            cell(kernel, n, lambda s: f"{s['instret'] / s['cycles']:.2f}")
            for n in BENCH_CONFIGS) + " |")

    # Branch prediction: the configurations that differ only in the predictor.
    inorder_configs = [n for n in BENCH_CONFIGS if bench_core(n) == "inorder"]
    ooo_configs = [n for n in BENCH_CONFIGS if bench_core(n) == "ooo"]
    ss_configs = [n for n in BENCH_CONFIGS if bench_core(n) == "ss"]
    predicted = [n for n in inorder_configs
                 if CONFIGS[n]["params"].get("BP_MODE")
                 and not CONFIGS[n]["params"].get("DC_PF_MODE")
                 and not CONFIGS[n]["params"].get("IC_PF_MODE")]
    if predicted:
        base = next(n for n in reversed(inorder_configs)
                    if not CONFIGS[n]["params"].get("BP_MODE"))
        compared = [base] + predicted
        lines += ["", "### Branch prediction", "",
                  f"`{base}` has no predictor: every taken branch and every jump is a",
                  "misprediction. The others add a predictor to the same machine. A",
                  "misprediction costs two cycles.", "",
                  "| Kernel | Branches | Jumps | " + " | ".join(
                      f"`{n}` branches mispredicted | jumps mispredicted" for n in compared)
                  + " |",
                  "| --- | ---: | ---: | " + " | ".join("---: | ---:" for _ in compared) + " |"]
        for kernel in kernels:
            first = table.get((kernel, base))
            if not first:
                continue
            cells = []
            for n in compared:
                s = table.get((kernel, n))
                cells.append("- | -" if not s else
                             f"{s['branch_mis']} ({rate(s['branch_mis'], s['branches'])}) | "
                             f"{s['jump_mis']} ({rate(s['jump_mis'], s['jumps'])})")
            lines.append(f"| `{kernel}` | {first['branches']} | {first['jumps']} | "
                         + " | ".join(cells) + " |")

    # Prefetching: the configurations that differ only in the prefetchers.
    prefetching = [n for n in inorder_configs
                   if CONFIGS[n]["params"].get("DC_PF_MODE")
                   or CONFIGS[n]["params"].get("IC_PF_MODE")]
    if prefetching:
        base = next(n for n in reversed(inorder_configs) if n not in prefetching)

        def pf_cells(s, side):
            issued, useful, late = (s[f"{side}_pf_issued"], s[f"{side}_pf_useful"],
                                    s[f"{side}_pf_late"])
            misses = s[f"{side}_miss"]
            return (f"{misses} | {issued} | {rate(useful, issued)} | "
                    f"{rate(useful, useful + misses)} | {rate(useful - late, useful)}")

        for side, title, which in (("dc", "Data prefetching", "data cache"),
                                   ("ic", "Instruction prefetching", "instruction cache")):
            shown = [n for n in prefetching
                     if CONFIGS[n]["params"].get(f"{side.upper()}_PF_MODE")]
            if side == "ic":
                shown = shown[:1]           # the instruction prefetcher is the same in all
            if not shown:
                continue
            lines += ["", f"### {title}", "",
                      f"`{base}` has no prefetcher. Misses are accesses that had to wait for",
                      f"the {which} to fetch a line that was not on its way already.",
                      "Accuracy = prefetched lines that were used / prefetches issued.",
                      "Coverage = prefetched lines used / (those + remaining misses).",
                      "Timely = share of the used prefetches that arrived before they were",
                      "needed.", "",
                      f"| Kernel | `{base}` misses | " + " | ".join(
                          f"`{n}` misses | issued | accuracy | coverage | timely"
                          for n in shown) + " |",
                      "| --- | ---: | " + " | ".join(
                          "---: | ---: | ---: | ---: | ---:" for _ in shown) + " |"]
            for kernel in kernels:
                first = table.get((kernel, base))
                if not first:
                    continue
                # Leave out kernels the prefetcher has nothing to do with.
                if side == "ic" and first["ic_miss"] < 50:
                    continue
                if side == "dc" and first["dc_miss"] < 50:
                    continue
                cells = [pf_cells(table[(kernel, n)], side) if (kernel, n) in table
                         else "- | - | - | - | -" for n in shown]
                lines.append(f"| `{kernel}` | {first[side + '_miss']} | "
                             + " | ".join(cells) + " |")

    # Out-of-order execution: the same machine with each core.
    if ooo_configs:
        base = inorder_configs[-1]
        compared = [base] + ooo_configs
        lines += ["", "### Out-of-order execution", "",
                  f"`{base}` is the in-order core; the others are the out-of-order core",
                  "with the same caches, predictor and prefetchers. Cycles relative to",
                  "the in-order core are in brackets: below 1.00 is faster.", "",
                  "| Kernel | Instructions | " + " | ".join(
                      f"`{n}` cycles | IPC | mispredictions" for n in compared) + " |",
                  "| --- | ---: | " + " | ".join("---: | ---: | ---:" for _ in compared) + " |"]
        for kernel in kernels:
            first = table.get((kernel, base))
            if not first:
                continue
            cells = []
            for n in compared:
                s = table.get((kernel, n))
                if not s:
                    cells.append("- | - | -")
                    continue
                relative = "" if n == base else f" ({s['cycles'] / first['cycles']:.2f})"
                cells.append(f"{s['cycles']}{relative} | {s['instret'] / s['cycles']:.2f} | "
                             f"{s['redirects']}")
            lines.append(f"| `{kernel}` | {first['instret']} | " + " | ".join(cells) + " |")

    # Superscalar execution: the same machine, one and two instructions wide.
    if ss_configs and ooo_configs:
        base = ooo_configs[-1]
        compared = [base] + ss_configs
        lines += ["", "### Superscalar execution", "",
                  f"`{base}` handles one instruction per cycle; the others are the same",
                  "core and the same machine at two per cycle. Cycles relative to the",
                  "one-wide core are in brackets: below 1.00 is faster. An IPC above 1",
                  "is only possible at width 2.", "",
                  "| Kernel | Instructions | " + " | ".join(
                      f"`{n}` cycles | IPC | mispredictions" for n in compared) + " |",
                  "| --- | ---: | " + " | ".join("---: | ---: | ---:" for _ in compared) + " |"]
        for kernel in kernels:
            first = table.get((kernel, base))
            if not first:
                continue
            cells = []
            for n in compared:
                s = table.get((kernel, n))
                if not s:
                    cells.append("- | - | -")
                    continue
                relative = "" if n == base else f" ({s['cycles'] / first['cycles']:.2f})"
                cells.append(f"{s['cycles']}{relative} | {s['instret'] / s['cycles']:.2f} | "
                             f"{s['redirects']}")
            lines.append(f"| `{kernel}` | {first['instret']} | " + " | ".join(cells) + " |")

    # Every step together: what each one bought, kernel by kernel.
    steps = [("nocache", "dm", "Caches"),
             ("dm", "pf-stride", "2-way data cache, predictor, prefetchers"),
             ("pf-stride", "ooo-32", "Out-of-order"),
             ("ooo-32", "ss", "Two wide"),
             ("nocache", "ss", "All together")]
    if all(a in BENCH_CONFIGS and b in BENCH_CONFIGS for a, b, _ in steps):
        lines += ["", "### Step by step", "",
                  "How many times faster each step made each kernel: cycles before",
                  "divided by cycles after, so above 1.00 is faster. The last row is the",
                  "geometric mean over the kernels.", "",
                  "| Kernel | " + " | ".join(f"{what} (`{a}` to `{b}`)" for a, b, what in steps)
                  + " |",
                  "| --- | " + " | ".join("---:" for _ in steps) + " |"]
        ratios = {index: [] for index in range(len(steps))}
        for kernel in kernels:
            cells = []
            for index, (a, b, _) in enumerate(steps):
                before, after = table.get((kernel, a)), table.get((kernel, b))
                if before and after:
                    ratios[index].append(before["cycles"] / after["cycles"])
                    cells.append(f"{ratios[index][-1]:.2f}")
                else:
                    cells.append("-")
            lines.append(f"| `{kernel}` | " + " | ".join(cells) + " |")
        means = [f"**{math.exp(sum(map(math.log, r)) / len(r)):.2f}**" if r else "-"
                 for r in ratios.values()]
        lines.append("| Geometric mean | " + " | ".join(means) + " |")

    cached = [n for n in ("dm", "2way") if n in BENCH_CONFIGS]
    if cached:
        lines += ["", "### Data cache", "",
                  "Miss rate is misses per data access. Stall is the share of all cycles",
                  "the pipeline spent held waiting for the data cache.", "",
                  "| Kernel | Data accesses | " + " | ".join(
                      f"`{n}` misses | miss rate | stall | write-backs" for n in cached) + " |",
                  "| --- | ---: | " + " | ".join("---: | ---: | ---: | ---:" for _ in cached) + " |"]
        for kernel in kernels:
            first = table.get((kernel, cached[0]))
            cells = []
            for n in cached:
                s = table.get((kernel, n))
                cells.append("- | - | - | -" if not s else
                             f"{s['dc_miss']} | {rate(s['dc_miss'], s['dc_access'])} | "
                             f"{percent(s['hold'], s['cycles'])} | {s['dc_writeback']}")
            lines.append(f"| `{kernel}` | {first['dc_access'] if first else ''} | "
                         + " | ".join(cells) + " |")

        name = cached[0]
        lines += ["", f"### Instruction cache (`{name}`)", "",
                  "| Kernel | Fetches | Misses | Miss rate | Cycles fetch waited |",
                  "| --- | ---: | ---: | ---: | ---: |"]
        for kernel in kernels:
            s = table.get((kernel, name))
            if s:
                lines.append(f"| `{kernel}` | {s['ic_access']} | {s['ic_miss']} | "
                             f"{rate(s['ic_miss'], s['ic_access'])} | "
                             f"{percent(s['ifwait'], s['cycles'])} |")

    lines += ["", "### Where the cycles go on the ideal machine", "",
              "| Kernel | Cycles | Taken branches and jumps | Load-use stalls | "
              "Cycles lost to control flow | Cycles lost to load-use |",
              "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for kernel in kernels:
        s = table.get((kernel, "ideal"))
        if s:
            lines.append(f"| `{kernel}` | {s['cycles']} | {s['redirects']} | {s['loaduse']} | "
                         f"{percent(2 * s['redirects'], s['cycles'])} | "
                         f"{percent(s['loaduse'], s['cycles'])} |")
    lines += policy_markdown(all_kernels, table)
    lines += hierarchy_markdown(all_kernels, table, stairs or {})
    return "\n".join(lines) + "\n"


def policy_markdown(kernels, table):
    """The cache-policy part of the results: replacement and write policy."""
    def rate(part, whole):
        return f"{100 * part / whole:.1f}%" if whole else "-"

    def have(kernel, names):
        return all((kernel, name) in table for name in names)

    repl_names = {0: "LRU", 1: "FIFO", 2: "random", 3: "pseudo-LRU"}
    lines = ["", "### Cache policies: configurations", "",
             "All on the in-order core with the predictor of `bp2`, no prefetchers,",
             "memory latency 10, a 1 KiB direct-mapped instruction cache and a 1 KiB",
             "data cache of 8 sets x 4 ways. `lru` is the base; each of the others",
             "changes what the table shows.", "",
             "| Name | Replacement | A store that hits | A store that misses | Write buffer |",
             "| --- | --- | --- | --- | --- |"]
    for name in POLICY_CONFIGS:
        params = CONFIGS[name]["params"]
        entries = params.get("DC_WBUF_ENTRIES", 0)
        buffer = "none" if not entries else (
            f"{entries} entries" + (", merging" if params.get("DC_WBUF_MERGE") else ""))
        lines.append(
            f"| `{name}` | {repl_names[params.get('DC_REPL', 0)]} "
            f"| {'write-through' if params.get('DC_WRITE_HIT') else 'write-back'} "
            f"| {'no-allocate' if params.get('DC_WRITE_MISS') else 'allocate'} | {buffer} |")

    # Replacement: kernels on which the policies do not all miss equally often.
    shown = [k for k in kernels if have(k, REPL_CONFIGS)
             and len({table[(k, n)]["dc_miss"] for n in REPL_CONFIGS}) > 1]
    same = [k for k in kernels if have(k, REPL_CONFIGS) and k not in shown]
    lines += ["", "### Replacement policy", "",
              "Data cache misses, miss rate, and cycles; cycles relative to `lru` in",
              "brackets (below 1.00 is faster)."]
    if same:
        lines += ["On " + ", ".join(f"`{k}`" for k in same) + " all four policies",
                  "miss exactly as often, and those kernels are left out."]
    lines += ["", "| Kernel | Data accesses | " + " | ".join(
        f"`{n}` misses | cycles" for n in REPL_CONFIGS) + " |",
              "| --- | ---: | " + " | ".join("---: | ---:" for _ in REPL_CONFIGS) + " |"]
    for kernel in shown:
        base = table[(kernel, REPL_CONFIGS[0])]
        cells = []
        for name in REPL_CONFIGS:
            s = table[(kernel, name)]
            relative = "" if name == REPL_CONFIGS[0] else f" ({s['cycles'] / base['cycles']:.2f})"
            cells.append(f"{s['dc_miss']} ({rate(s['dc_miss'], s['dc_access'])}) | "
                         f"{s['cycles']}{relative}")
        lines.append(f"| `{kernel}` | {base['dc_access']} | " + " | ".join(cells) + " |")

    def mean(names, name, base_name):
        ratios = [table[(k, name)]["cycles"] / table[(k, base_name)]["cycles"]
                  for k in kernels if have(k, names)]
        return math.exp(sum(map(math.log, ratios)) / len(ratios)) if ratios else 1.0

    counted = sum(have(k, REPL_CONFIGS) for k in kernels)
    lines += ["", f"Geometric mean of the cycles relative to `lru`, over all {counted} kernels: "
              + ", ".join(f"`{n}` {mean(REPL_CONFIGS, n, 'lru'):.3f}" for n in REPL_CONFIGS[1:])
              + "."]

    # Write policy: kernels that send anything to memory under write-through.
    shown = [k for k in kernels if have(k, WRITE_CONFIGS)
             and table[(k, "wt")]["dc_mem_write"] >= 100]
    quiet = [k for k in kernels if have(k, WRITE_CONFIGS) and k not in shown]
    header = "| Kernel | " + " | ".join(f"`{n}`" for n in WRITE_CONFIGS) + " |"
    rule = "| --- | " + " | ".join("---:" for _ in WRITE_CONFIGS) + " |"
    lines += ["", "### Write policy: cycles", "",
              "`lru` is write-back with allocation and no buffer. Cycles relative to",
              "it are in brackets (below 1.00 is faster)."]
    if quiet:
        lines += ["Kernels that store almost nothing ("
                  + ", ".join(f"`{k}`" for k in quiet) + ") are left out."]
    lines += ["", header, rule]
    for kernel in shown:
        base = table[(kernel, WRITE_CONFIGS[0])]
        cells = [str(base["cycles"])] + [
            f"{table[(kernel, n)]['cycles']} "
            f"({table[(kernel, n)]['cycles'] / base['cycles']:.2f})"
            for n in WRITE_CONFIGS[1:]]
        lines.append(f"| `{kernel}` | " + " | ".join(cells) + " |")
    counted = sum(have(k, WRITE_CONFIGS) for k in kernels)
    lines.append(f"| Geometric mean, all {counted} kernels | 1.00 | " + " | ".join(
        f"**{mean(WRITE_CONFIGS, n, 'lru'):.2f}**" for n in WRITE_CONFIGS[1:]) + " |")

    lines += ["", "### Write policy: traffic to and from memory", "",
              "For each configuration: writes sent to memory / lines read from memory",
              "by the data cache. A write is a whole line when a dirty line is",
              "evicted, and the bytes of one store (or of several merged ones)",
              "otherwise.", "", header, rule]
    for kernel in shown:
        cells = [f"{table[(kernel, n)]['dc_mem_write']} / {table[(kernel, n)]['dc_mem_read']}"
                 for n in WRITE_CONFIGS]
        lines.append(f"| `{kernel}` | " + " | ".join(cells) + " |")

    buffered = [n for n in WRITE_CONFIGS if CONFIGS[n]["params"].get("DC_WBUF_ENTRIES")]
    lines += ["", "### Write buffer", "",
              "Share of all cycles in which a store waited because the write buffer",
              "was full.", "",
              "| Kernel | " + " | ".join(f"`{n}`" for n in buffered) + " |",
              "| --- | " + " | ".join("---:" for _ in buffered) + " |"]
    for kernel in shown:
        cells = [rate(table[(kernel, n)]["dc_wbuf_full"], table[(kernel, n)]["cycles"])
                 for n in buffered]
        lines.append(f"| `{kernel}` | " + " | ".join(cells) + " |")
    return lines


def hierarchy_markdown(kernels, table, stairs):
    """The hierarchy part of the results: victim buffer, second level,
    inclusion, access time and the staircase."""
    def rate(part, whole):
        return f"{100 * part / whole:.1f}%" if whole else "-"

    def have(kernel, names):
        return all((kernel, name) in table for name in names)

    def geomean(names, name, base_name):
        ratios = [table[(k, name)]["cycles"] / table[(k, base_name)]["cycles"]
                  for k in kernels if have(k, names)]
        return math.exp(sum(map(math.log, ratios)) / len(ratios)) if ratios else 1.0

    lines = []

    # ---- victim buffer beside a direct-mapped cache ----
    if all(name in CONFIGS for name in VICTIM_CONFIGS):
        base, with_vb, two_way = VICTIM_CONFIGS
        entries = CONFIGS[with_vb]["params"].get("DC_VICTIM_ENTRIES", 0)
        shown = [k for k in kernels if have(k, VICTIM_CONFIGS)
                 and table[(k, with_vb)]["dc_vb_hit"] >= 10]
        counted = sum(have(k, VICTIM_CONFIGS) for k in kernels)
        lines += ["", "### Victim buffer", "",
                  f"`{with_vb}` is `{base}` with a {entries}-entry victim buffer beside the "
                  "data cache;",
                  f"`{two_way}` is the same cache size in two ways instead. Misses are data",
                  "accesses that did not find their line in the cache; with the buffer,",
                  "the number in brackets is how many of them the buffer served, each in",
                  "one cycle and without going to memory. Cycles relative to",
                  f"`{base}` are in brackets (below 1.00 is faster). Kernels in which the",
                  "buffer served fewer than ten misses are left out.", "",
                  f"| Kernel | Data accesses | `{base}` misses | cycles | `{with_vb}` misses "
                  f"(from the buffer) | cycles | `{two_way}` misses | cycles |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for kernel in shown:
            first, vb, two = (table[(kernel, n)] for n in VICTIM_CONFIGS)
            lines.append(
                f"| `{kernel}` | {first['dc_access']} | {first['dc_miss']} | {first['cycles']} "
                f"| {vb['dc_miss']} ({vb['dc_vb_hit']}) "
                f"| {vb['cycles']} ({vb['cycles'] / first['cycles']:.2f}) "
                f"| {two['dc_miss']} | {two['cycles']} ({two['cycles'] / first['cycles']:.2f}) |")
        lines += ["", f"Geometric mean of the cycles relative to `{base}`, over all {counted} "
                  f"kernels: `{with_vb}` {geomean(VICTIM_CONFIGS, with_vb, base):.3f}, "
                  f"`{two_way}` {geomean(VICTIM_CONFIGS, two_way, base):.3f}."]

    # ---- the slow-memory machine ----
    pf_names = {0: "none", 1: "next line", 2: "stride", 3: "random"}
    lines += ["", "### The hierarchy: configurations", "",
              "All on the in-order core with the predictor of `bp2`, a 1 KiB",
              "direct-mapped instruction cache and a 1 KiB data cache of 8 sets x 4",
              "ways (the caches of `lru`), and a memory that takes 50 cycles per line",
              "instead of 10. The second-level cache is shared by both first-level",
              "caches, uses LRU and write-back, and answers a hit 4 cycles after it is",
              "asked.", "",
              "| Name | Second-level cache | Inclusion | Victim buffer (data) "
              "| Prefetch (instructions / data) |",
              "| --- | --- | --- | --- | --- |"]
    for name in HIER_CONFIGS:
        params = CONFIGS[name]["params"]
        if params.get("L2_ENABLE"):
            sets, ways = params.get("L2_SETS", 64), params.get("L2_WAYS", 4)
            second = f"{sets * ways * 32 // 1024} KiB, {sets} sets x {ways} ways"
            inclusion = "inclusive" if params.get("L2_INCLUSION") else "non-inclusive"
        else:
            second, inclusion = "none", "-"
        buffer = params.get("DC_VICTIM_ENTRIES", 0)
        lines.append(
            f"| `{name}`: {CONFIGS[name]['label']} | {second} | {inclusion} "
            f"| {f'{buffer} entries' if buffer else 'none'} "
            f"| {pf_names[params.get('IC_PF_MODE', 0)]} / {pf_names[params.get('DC_PF_MODE', 0)]} |")

    base = HIER_CONFIGS[0]
    header = "| Kernel | " + " | ".join(f"`{n}`" for n in HIER_CONFIGS) + " |"
    rule = "| --- | " + " | ".join("---:" for _ in HIER_CONFIGS) + " |"
    shown = [k for k in kernels if have(k, HIER_CONFIGS)]
    lines += ["", "### The hierarchy: cycles", "",
              f"Cycles relative to `{base}` are in brackets (below 1.00 is faster). The",
              "last row is the geometric mean over the kernels.", "", header, rule]
    for kernel in shown:
        first = table[(kernel, base)]
        cells = [str(first["cycles"])] + [
            f"{table[(kernel, n)]['cycles']} ({table[(kernel, n)]['cycles'] / first['cycles']:.2f})"
            for n in HIER_CONFIGS[1:]]
        lines.append(f"| `{kernel}` | " + " | ".join(cells) + " |")
    lines.append(f"| Geometric mean, all {len(shown)} kernels | 1.00 | " + " | ".join(
        f"**{geomean(HIER_CONFIGS, n, base):.2f}**" for n in HIER_CONFIGS[1:]) + " |")

    def asked(st):
        """Lines the first-level caches asked the second level to read."""
        return st["ic_mem_read"] + st["dc_mem_read"]

    def read_misses(st):
        """... and how many of them the second level did not have."""
        return st["l2_miss"] - st["l2_store_miss"]

    # ---- where the misses go ----
    plain, inclusive = "h-l2", "h-incl"
    if plain in HIER_CONFIGS and inclusive in HIER_CONFIGS:
        lines += ["", "### The hierarchy: misses at each level", "",
                  "Lines asked for are the lines the two first-level caches requested",
                  "from the second level: their misses. Missed there is how many of",
                  "those the second level did not have either, and the rate is local:",
                  "per line it was asked for. Lines taken back are those an inclusive",
                  "second level removed from a first-level cache in order to evict them",
                  "itself.", "",
                  f"| Kernel | `{plain}` lines asked for | missed there "
                  f"| `{inclusive}` lines asked for | missed there | lines taken back |",
                  "| --- | ---: | ---: | ---: | ---: | ---: |"]
        for kernel in shown:
            a, b = table[(kernel, plain)], table[(kernel, inclusive)]
            lines.append(
                f"| `{kernel}` | {asked(a)} "
                f"| {read_misses(a)} ({rate(read_misses(a), asked(a))}) "
                f"| {asked(b)} | {read_misses(b)} ({rate(read_misses(b), asked(b))}) "
                f"| {b['ic_inval'] + b['dc_inval']} |")

    # ---- access time ----
    def penalties(name):
        """Cycles a first-level miss waits when the second level hits, and
        how many more when it misses."""
        cfg = CONFIGS[name]
        return cfg["params"].get("L2_HIT_LATENCY", 4) + 2, cfg["latency"] + 2

    def formula(st, t2, tmem):
        """t1 + m1 x (t2 + m2 x tmem), and the two miss rates."""
        m1 = st["dc_miss"] / st["dc_access"]
        m2 = read_misses(st) / asked(st) if asked(st) else 0.0
        return 1 + m1 * (t2 + m2 * tmem), m1, m2

    if plain in HIER_CONFIGS:
        t2, tmem = penalties(plain)
        lines += ["", "### Time per data access: measured and computed", "",
                  f"On `{plain}`. Measured: 1 + the cycles the core waited for the data",
                  "cache, divided by its accesses. Formula: the access time of lecture",
                  f"21, `T1 = t1 + m1 x (t2 + m2 x tmem)`, with t1 = 1 cycle, t2 = {t2}",
                  "(what a first-level miss waits when the second level hits: its 4",
                  f"cycles and 2 in the first-level cache) and tmem = {tmem} more when it",
                  "misses (memory's 50 and 2 in the second-level cache). m1 is the data",
                  "cache's miss rate. m2 is the second level's, counted as the lecture",
                  "defines it, over the lines it was asked for by the level above. The",
                  "last column adds what the formula leaves out and the counters know:",
                  "every dirty line written back, at either level, to a level that takes",
                  f"{t2 - 1} or {tmem - 1} cycles to accept it. Kernels with fewer than 100 "
                  "data-cache",
                  "misses are left out.", "",
                  "| Kernel | Data accesses | m1 | m2 | Measured | Formula "
                  "| Formula with write-backs |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for kernel in shown:
            st = table[(kernel, plain)]
            if st["dc_miss"] < 100:
                continue
            value, m1, m2 = formula(st, t2, tmem)
            measured = 1 + st["dc_wait"] / st["dc_access"]
            extra = (st["dc_writeback"] * (t2 - 1)
                     + st["l2_writeback"] * (tmem - 1)) / st["dc_access"]
            lines.append(f"| `{kernel}` | {st['dc_access']} | {100 * m1:.1f}% | {100 * m2:.1f}% "
                         f"| {measured:.2f} | {value:.2f} | {value + extra:.2f} |")

    # ---- the staircase ----
    def size_text(size):
        return f"{size} B" if size < 1024 else f"{size // 1024} KiB"

    def step(size, name):
        """What the second half of the longer run cost: the counters of the
        long run minus those of the short one."""
        short = stairs.get((size, WS_READS, name))
        long = stairs.get((size, 2 * WS_READS, name))
        if not short or not long:
            return None
        return {key: long[key] - short[key] for key in long
                if isinstance(long[key], int) and isinstance(short.get(key), int)}

    if stairs:
        t2, tmem = penalties("h-l2")
        lines += ["", "### Working-set size: the staircase", "",
                  "`ws_walk` reads one doubleword per line over a working set of the",
                  "given size, round and round. Each size is run twice, with "
                  f"{WS_READS} and with",
                  f"{2 * WS_READS} loads; the figures are the difference between the two "
                  "runs, that is,",
                  f"what the last {WS_READS} loads cost with the caches warm. Time per "
                  "load is 1 + the",
                  "cycles waited for the data cache per load. The data cache holds 1 KiB",
                  "and the second-level cache 8 KiB, which it shares with the program's",
                  "own code. Computed is the formula of the table above. The last two",
                  "columns are the same machine with an inclusive second level.", "",
                  "| Working set | `h-l1` miss rate | time per load "
                  "| `h-l2` first-level miss rate | second-level miss rate | time per load "
                  "| computed | `h-incl` time per load | lines taken back |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for size in WS_SIZES:
            one, two, three = (step(size, name) for name in ("h-l1", "h-l2", "h-incl"))
            if not one or not two or not three:
                continue
            value, _, _ = formula(two, t2, tmem)
            lines.append(
                f"| {size_text(size)} | {rate(one['dc_miss'], one['dc_access'])} "
                f"| {1 + one['dc_wait'] / one['dc_access']:.2f} "
                f"| {rate(two['dc_miss'], two['dc_access'])} "
                f"| {rate(read_misses(two), asked(two))} "
                f"| {1 + two['dc_wait'] / two['dc_access']:.2f} | {value:.2f} "
                f"| {1 + three['dc_wait'] / three['dc_access']:.2f} "
                f"| {three['ic_inval'] + three['dc_inval']} |")
    return lines


def sweep_configs():
    """The RTL grid of the sweep stage: {name: configuration}."""
    def machine(label, latency=10, **params):
        return config(label, latency=latency, cores=("inorder",), **predictor(), **params)

    grid = {}
    for size in sweep.RTL_SIZES:
        for ways in sweep.RTL_WAYS:
            grid[f"g-{size}-{ways}"] = machine(
                f"{size}-byte data cache, {ways} ways",
                **caches(32, 1, size // sweep.LINE // ways, ways))
    for line in sweep.LINES:
        if line != sweep.LINE:
            grid[f"b-{line}"] = machine(
                f"{line}-byte lines", LINE_BYTES=line,
                **caches(1024 // line, 1, 1024 // line // 4, 4))
    for name, code in (("fifo", FIFO), ("random", RAND), ("plru", PLRU)):
        grid[f"r-{name}"] = machine(f"{name} replacement", **caches(32, 1, 8, 4),
                                    **policy(code, i_repl=LRU))
    for (name, label), (hit, miss) in zip(sweep.WRITE, ((BACK, ALLOCATE), (THROUGH, ALLOCATE),
                                                         (BACK, AROUND), (THROUGH, AROUND))):
        grid[name] = machine(label, **caches(32, 1, 8, 4),
                             **policy(hit=hit, miss=miss, wbuf=4))
    for size in sweep.IC_SIZES:
        if size != 1024:
            grid[f"i-{size}"] = machine(f"{size}-byte instruction cache",
                                        **caches(size // sweep.LINE, 1, 8, 4))
    grid["l-none"] = machine("slow memory, no second level", latency=50,
                             **caches(32, 1, 8, 4))
    for kib in sweep.L2_KIB:
        for kind, inclusive in (("n", False), ("i", True)):
            grid[f"l-{kib}-{kind}"] = machine(
                f"{kib} KiB second level" + (", inclusive" if inclusive else ""), latency=50,
                **caches(32, 1, 8, 4),
                **l2(kib * 1024 // sweep.LINE // 4, 4, inclusive=inclusive))
    return grid


# The grid's machines are configurations like any other, so that one of them can
# be run by name (-c) when a point of the grid needs looking at.
SWEEP_CONFIGS = sweep_configs()
CONFIGS.update(SWEEP_CONFIGS)


def stage_sweep(args):
    data_file = os.path.join(ROOT, "docs", "data", "sweeps.json")
    if args.tables_only:
        try:
            with open(data_file, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return [("sweep: document", False, f"no {rel(data_file)}; run the sweep stage first")]
        written = sweep.write_outputs(ROOT, data)
        return [("sweep: document and charts rebuilt", True, f"{len(written)} files")]
    if not have_icarus():
        return [("sweep", False, "iverilog/vvp not found on PATH")]
    # The document needs every kernel at every point, so -k does not apply.
    everything = argparse.Namespace(k=None, max=args.max)
    kernels = program_sources(everything, ["bench"])
    goldens = prepare_goldens(kernels, everything)
    recorded = {label[6:-2]: sweep.record(path, args.max) for label, path in kernels}
    grid = SWEEP_CONFIGS
    if args.c:                              # part of the grid: no document
        grid = {name: grid[name] for name in args.c.split(",") if name in grid}
    try:
        built = dict(zip(grid, pool_map(lambda name: build_core("inorder", name), grid)))
    except RuntimeError as err:
        return [("sweep: build", False, str(err))]

    def run(job):
        """One kernel at one point of the grid: the usual checks, then the
        model on its own against the RTL's counters."""
        name, (label, _) = job
        ok, detail, stats = check_on_rtl(built[name], "inorder", name, label, goldens[label],
                                         keep_logs=("ic",))
        if not ok:
            return False, detail, {}, 0
        params = CONFIGS[name]["params"]
        models = cache_models(params)
        counters = {key: value for key, value in stats.items() if isinstance(value, int)}
        counters.update(stats["final"])
        log = stats["logs"]["ic"]
        with open(log, encoding="ascii") as handle:
            fetched = sweep.replay_fetches(handle, **models["ic"])
        os.remove(log)
        wrong = [f"ic_{key}: RTL {counters.get('ic_' + key)}, model {fetched[key]}"
                 for key in ("miss", "evict", "mem_read", "inval")
                 if counters.get("ic_" + key) != fetched[key]]
        data_tied = not params.get("L2_INCLUSION")
        if data_tied:
            count = sweep.model_counters(recorded[label[6:-2]].data, **models["dc"])
            wrong += [f"dc_{key}: RTL {counters.get('dc_' + key)}, model {count[key]}"
                      for key in sweep.TIED if counters.get("dc_" + key) != count[key]]
        if wrong:
            return False, "the model on its own disagrees with the RTL: " + "; ".join(wrong), {}, 0
        return True, detail, {key: counters[key] for key in sweep.KEPT if key in counters}, \
            int(data_tied)

    jobs = [(name, kernel) for name in grid for kernel in kernels]
    outcomes = pool_map(run, jobs)
    results, rtl = [], {name: {} for name in grid}
    for name in grid:
        mine = [(label, outcome) for (cfg_name, (label, _)), outcome in zip(jobs, outcomes)
                if cfg_name == name]
        failed = [(label, outcome) for label, outcome in mine if not outcome[0]]
        for label, (ok, _, counters, _) in mine:
            if ok:
                rtl[name][label[6:-2]] = counters
        results.append((f"sweep {name}: {len(mine)} kernels", not failed,
                        f"{len(failed)} of {len(mine)} failed" if failed else
                        f"{sum(o[2]['cycles'] for _, o in mine)} cycles; model and RTL agree"))
        for label, (_, detail, _, _) in failed[:8]:
            results.append((f"sweep {name}: {label}", False, detail))
    if any(ok is False for _, ok, _ in results):
        results.append(("sweep: document", False, "not written, because runs failed"))
        return results
    if args.c:
        results.append(("sweep: document", None, "not written: -c ran part of the grid"))
        return results
    data = {
        "inputs": {label[6:-2]: sweep.source_hash(path) for label, path in kernels},
        "streams": sweep.stream_facts(recorded),
        "model": sweep.model_results(recorded),
        "rtl": rtl,
        "agreement": {"summary": {"runs": len(jobs), "fetch_points": len(jobs),
                                  "data_points": sum(o[3] for o in outcomes)}},
    }
    sweep.save_data(ROOT, data)             # first, in case drawing fails
    written = sweep.write_outputs(ROOT, data)
    results.append(("sweep: document and charts", True,
                    ", ".join(rel(path) for path in written[:2]) + f" and {len(written) - 2} charts"))
    return results


def update_results_doc(markdown):
    path = os.path.join(ROOT, "docs", "RESULTS.md")
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    pattern = re.compile(r"(<!-- BEGIN GENERATED -->\n).*?(<!-- END GENERATED -->)", re.S)
    if not pattern.search(text):
        raise SystemExit("docs/RESULTS.md has no GENERATED markers")
    text = pattern.sub(lambda m: m.group(1) + "\n" + markdown + "\n" + m.group(2), text)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print("updated docs/RESULTS.md")


STAGES = {"unit": stage_unit, "sw": stage_sw, "blocks": stage_blocks, "rtl": stage_rtl,
          "bench": stage_bench, "sweep": stage_sweep}
DEFAULT_STAGES = ["unit", "sw", "blocks", "rtl"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("stages", nargs="*", default=[],
                        help=f"stages to run: {', '.join(STAGES)} "
                             f"(default: {', '.join(DEFAULT_STAGES)})")
    parser.add_argument("-k", help="only run tests whose name contains this text")
    parser.add_argument("-c", metavar="CONFIG[,CONFIG...]",
                        help="rtl stage: only these configurations (known: "
                             + ", ".join(c for c in CONFIGS if c not in SWEEP_CONFIGS)
                             + "); sweep stage: only these machines of its grid")
    parser.add_argument("--core", choices=sorted(CORES),
                        help="rtl stage: only this core (default: all)")
    parser.add_argument("-v", action="store_true",
                        help="rtl stage: list every program instead of one line per group")
    parser.add_argument("--max", type=int, default=2_000_000,
                        help="golden-model instruction limit per program")
    parser.add_argument("--seeds", type=int, default=100,
                        help="number of random programs in the rtl stage (default 100)")
    parser.add_argument("--seed-base", type=int, default=0,
                        help="first random seed (default 0)")
    parser.add_argument("--full", action="store_true",
                        help="rtl stage: every kernel in every configuration "
                             "(default: a tenth of them outside the measured machines)")
    parser.add_argument("--no-cache-check", action="store_true",
                        help="rtl stage: do not replay the caches' event logs "
                             "against the cache model")
    parser.add_argument("--update-results", action="store_true",
                        help="bench stage: rewrite the generated part of docs/RESULTS.md")
    parser.add_argument("--tables-only", action="store_true",
                        help="bench and sweep stages: rebuild the tables (and charts) "
                             "from the counters of the last run instead of simulating")
    args = parser.parse_args(argv)
    unknown = [s for s in args.stages if s not in STAGES]
    if unknown:
        parser.error(f"unknown stage(s): {', '.join(unknown)}")
    unknown = [c for c in (args.c.split(",") if args.c else []) if c not in CONFIGS]
    if unknown:
        parser.error(f"unknown configuration(s): {', '.join(unknown)}")
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(errors="replace")

    # A result is (name, ok, detail); ok is None for a test that was skipped.
    passed = failed = skipped = 0
    for stage in args.stages or DEFAULT_STAGES:
        print(f"== {stage} ==")
        for name, ok, detail in STAGES[stage](args):
            passed += ok is True
            failed += ok is False
            skipped += ok is None
            status = "SKIP" if ok is None else "PASS" if ok else "FAIL"
            print(f"  {status}  {name:<44} {detail}")
    print(f"\n{passed}/{passed + failed} passed"
          + (f", {failed} FAILED" if failed else "")
          + (f", {skipped} skipped" if skipped else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
