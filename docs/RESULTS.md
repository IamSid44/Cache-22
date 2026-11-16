# Results

What each component of the processor buys, measured on the benchmark kernels
in [`sw/bench/`](../sw/bench/). All numbers come from simulation in Icarus
Verilog and are in clock cycles; nothing here says anything about clock
frequency.

Every run in these tables also passed the commit-trace comparison against
the golden model, so a number is only reported for a run that computed the
right answer. Since Phase 8 each run must also leave memory with the golden
model's contents, and each cache's event log must agree with the cache
model, so a miss count is only reported for a cache that followed its
policy. Since Phase 9 that includes the second-level cache, and an inclusive
hierarchy is only measured while it really is inclusive.

To regenerate the tables below after a change:

```text
python tools/regress.py bench --update-results
python tools/regress.py bench --tables-only --update-results   # from the last run's counters, without simulating
python tools/figures.py --collect                              # the charts below, from the same counters
```

The cache's parameters are swept across their range in
[SWEEPS.md](SWEEPS.md), which has its own command
(`python tools/regress.py sweep`); "Phase 10" below says what those sweeps
show.

## The short version

![What each step bought](img/speedup.svg)

- **The memory system matters most.** Caches in front of a 10-cycle memory
  are worth 6.0x. Everything done to the core afterwards is worth 1.28x.
- **Out-of-order execution alone is worth 3%.** It removes waiting, not
  work. It is what gives a second instruction per cycle something to do,
  and the two together reach 1.79 instructions per cycle on independent
  work and exactly the dependency limit, 1.12, on a serial chain.
- **A prediction has to be both accurate and timely.** A 2-bit predictor removes
  nearly every loop misprediction and half of an alternating branch's. A
  next-line prefetcher can be 99.8% accurate and never on time, and makes a
  strided walk 46% slower.
- **No cache policy is best everywhere.** FIFO needs 1.73 times the cycles
  of LRU on one kernel, and LRU misses every single time on another, where
  random replacement misses 40%.
- **A second cache level** takes the cycle count to 0.57 with a slow
  memory. A load costs 1, 7 or 59 cycles depending on which level its data
  fits in, as the textbook formula says to two decimal places.
- **Inclusion** costs 15% at the size measured, most of it by evicting the
  program's own loop, and nothing once the second level is large enough.
- **Swept across their range,** the cache's parameters behave as the
  textbook says on average and not in detail: more ways can mean more
  misses, and random replacement beats LRU on these kernels at every size.

The sections below take these one phase at a time, each with the kernel
built to show the effect and the kernel built to show its limit. The
reference tables come last.

## What the numbers show

### Phase 1: the in-order baseline

This is the `ideal` column: the pipeline with a memory that answers in the
same cycle. It is the starting line for the pipeline itself, and from
Phase 3 on it is the upper bound that the memory system tries to get back to.

- **The cycle count is fully explained by two effects.** For every kernel,
  cycles = instructions + 2 x (taken branches and jumps) + load-use stalls,
  plus a couple of cycles to fill the pipeline. For `array_sum`:
  46116 + 2 x 4607 + 0 = 55330, against 55332 measured.
- **Control flow is the main loss.** Every taken branch and every jump is
  discovered in the execute stage and throws away the two instructions
  fetched behind it. That costs `calls` 48% of its cycles, and `branch_alt`,
  `sieve` and `loop_nest` about a third each. This column is what branch
  prediction (Phase 4) attacks. `loop_nest` and `calls` should recover almost
  all of it; `branch_alt` is built so that a 2-bit counter recovers little.
- **Load-use stalls are the smaller loss**, at most 13% (`stride_walk`), and
  an in-order pipeline cannot avoid them.
- **`ilp_chains` and `dep_chain` are indistinguishable** (both IPC 0.90), as
  intended: they are the same work arranged as four independent chains or as
  one serial chain, and an in-order single-issue pipeline cannot tell the
  difference. They separate once the core is out-of-order and 2-wide.
- **The memory kernels show nothing yet.** Memory answers in the same cycle
  in this phase, so `array_sum`, `stride_walk`, `list_walk` and `conflict`
  behave like ordinary code. Phase 3 gives memory a realistic latency, which
  will make every number in this table worse before caches and prefetching
  win it back; that slower machine, not this one, is the honest baseline for
  the memory results.

### Phase 2: precise exceptions

No change, by design. Trap support adds a 3-cycle penalty per trap or
`mret`, and no kernel traps, so every cycle count is identical to
Phase 1. What this phase bought is correctness, not speed: the core now
handles every exception the platform defines, precisely, and that is
measured by the regression rather than by this table.

### Phase 3: memory latency and caches

`nocache` is the same pipeline with a realistic memory: 10 cycles for every
transfer. `dm` adds a 1 KiB direct-mapped instruction cache and a 1 KiB
direct-mapped data cache (32 lines of 32 bytes each). `2way` keeps the data
cache at 1 KiB but makes it 2-way set-associative.

- **Without caches the machine is about ten times slower.** Every fetch
  waits for memory, so IPC falls from 0.5-0.9 to 0.06-0.09 on every kernel,
  whatever the kernel does.
- **The instruction cache gives all of that back.** Each kernel's code fits
  in 1 KiB, so there are at most 9 instruction misses per run, all of them
  first-time fetches. The five kernels with little or no data traffic
  (`branch_alt`, `calls`, `dep_chain`, `ilp_chains`, `loop_nest`) run within
  0.3% of the ideal machine.
![The data cache: direct-mapped and two ways](img/caches.svg)

- **The data cache helps exactly as much as the access pattern allows:**

  | Kernel | Data miss rate (`dm`) | Why |
  | --- | ---: | --- |
  | `array_sum` | 25% | A sequential walk misses once per line and hits the other three doublewords: spatial locality. The 16 KiB array does not fit, so every pass misses again. |
  | `memcpy`, `matadd` | 19-25% | The same streaming pattern. |
  | `list_walk` | 50% | Nodes are scattered over 8 KiB; half the hops land on a line that is not cached. |
  | `stride_walk` | 100% | One doubleword every 64 bytes: no two accesses share a line and nothing is reused. A cache cannot help; a stride prefetcher (Phase 5) can. |
  | `conflict` | 100% | See below. |

- **Associativity: the `conflict` kernel.** Its two arrays total 1 KiB, so
  they fit in the cache, but they sit exactly 4096 bytes apart, so `A[i]`
  and `B[i]` always map to the same line of a direct-mapped cache. Reading
  them alternately evicts the other one every time: all 7,808 accesses miss
  and the pipeline spends 73% of its cycles waiting. With two ways the pair
  coexists: 32 misses (one per line, the first time), and the kernel runs
  3.6 times faster (130,550 cycles down to 35,830), within 1.3% of ideal.
  This is the A, B, A, B example of DDCA lecture 22.
- **Associativity does nothing for the other kernels**, whose misses come
  from capacity or from the access pattern, not from conflicts. `sieve` is
  marginally worse with two ways (886 misses against 865), because the same
  size in two ways means half as many sets.
- **Stall time is reported next to miss rate** because the two are not the
  same thing. `array_sum` misses on a quarter of its accesses but spends 52%
  of its cycles held: each miss costs about 13 cycles (12 to fetch the line,
  and one miss in nine also writes a dirty line back).
- **What is left for later phases.** `array_sum`, `memcpy`, `matadd` and
  `stride_walk` miss in a perfectly predictable pattern, which is what
  prefetching (Phase 5) is for. `list_walk` and `miss_indep` are not
  predictable; `miss_indep` is waiting for an out-of-order core (Phase 6) to
  do its independent work during the miss.

### Phase 4: branch prediction

`bp1` and `bp2` are the `2way` machine with a 64-entry branch target buffer
added. `bp1` predicts each branch to do what it did last time; `bp2` uses a
2-bit counter. Without a predictor every taken branch and every jump costs
two cycles.

![Branch prediction: wrong guesses per 1,000 instructions](img/branch.svg)

- **Loop branches are predicted almost perfectly.** `loop_nest` falls from 4,607 mispredictions to 160
  and its IPC rises from 0.67 to 0.98. The 160 are the loop exits: 144 runs
  of the inner loop, 12 of the middle one, one of the outer, and three
  first encounters.
- **Two bits against one, on loops.** The last-time predictor mispredicts
  `loop_nest` 314 times, almost exactly twice as often. It is wrong at each
  loop exit and then wrong again on the first iteration of the next run,
  because the exit flipped its single bit. The 2-bit counter needs two
  misses in a row to change its mind, so it is wrong only at the exit. This
  is the (N-2)/N against (N-1)/N of DDCA lecture 15b.
- **Two bits against one, on alternation.** `branch_alt` contains a branch
  that alternates taken and not taken, 2,500 times. Last-time gets every
  one of them wrong (2,501 mispredictions in the kernel); the 2-bit counter
  gets half wrong (1,252). Neither can learn the pattern, which is the
  limit this kernel exists to show: it needs a predictor that remembers
  history, and that is outside this project's scope.
- **Jumps.** Without a predictor `calls` mispredicts all 9,601 of its jumps
  and spends 48% of its time recovering. The BTB remembers where each jump
  went, which leaves 1,611: almost all of them the returns of the one
  function that is called from two places, whose return address alternates.
  IPC goes from 0.50 to 0.84. A return-address stack would remove the rest.
- **Data-dependent branches stay hard.** `bsearch` improves only from 37%
  to 25% mispredicted: the direction of a binary-search probe is close to a
  coin flip whatever the predictor remembers.
- **Compute-bound kernels now run at about one instruction per cycle.**
  `dep_chain` and `ilp_chains` reach 1.00, `loop_nest` and `conflict` 0.98.
  An in-order, one-instruction-wide pipeline cannot go faster; that ceiling
  is what Phases 6 and 7 are for.
- **Memory-bound kernels hardly move.** `array_sum` loses nearly all of its
  mispredictions and gains only from 0.40 to 0.43, because its time goes to
  cache misses. That is Phase 5's problem.

### Phase 5: prefetching

`pf-next` is the `bp2` machine with a next-line prefetcher on both caches.
`pf-stride` keeps next-line for instructions and uses a stride prefetcher
for data, which asks four strides ahead.

![Prefetching: cycles relative to no prefetching](img/prefetch.svg)

- **Sequential data is nearly free of misses.** `array_sum` drops from
  4,608 data misses to 9 with next-line prefetching and its IPC rises from
  0.43 to 0.70 (0.74 with the stride prefetcher). `matadd` goes from 0.56 to
  0.97 and `memcpy` from 0.36 to 0.59.
- **An accurate prefetch can still arrive late.** The next-line prefetches for
  `array_sum` are 99.8% accurate and 0% timely. Its loop uses up a line
  (four doublewords, ten instructions) about every 11 cycles, and a line
  takes 12 cycles to arrive, so a line asked for one line ahead is always
  still on its way when it is needed. It still helps, because the wait is a
  few cycles instead of 12. The stride prefetcher asks four lines ahead here
  and is 100% timely.
- **Beyond that, memory speed is the limit.** Even so, `array_sum` keeps 697 misses
  with the stride prefetcher (85% coverage): it wants a new line every 11
  cycles and the memory delivers one every 12. `stride_walk` is the sharper
  case. It needs a new line every six cycles or so, the memory can supply
  half of that, and the stride prefetcher covers 50% of its misses, taking
  IPC from 0.23 to 0.31. No prefetcher can do better without a faster
  memory.
- **A wrong prefetcher is worse than none.** `stride_walk` reads one
  doubleword every 64 bytes, so the line after the one just used is never
  wanted. The next-line prefetcher fetches it anyway, every time: 7,680
  prefetches, accuracy 0%, and the kernel becomes 46% *slower* (135,789
  cycles to 197,657), because each real miss has to wait for the useless
  fetch in front of it. The stride prefetcher learns the 64-byte step and
  turns the same kernel into a gain.
- **Pointer chasing gives a prefetcher nothing to learn.** On `list_walk` the
  next-line prefetcher is 7% accurate and costs 17%. The stride prefetcher
  finds no repeating stride, issues no prefetches at all, and costs nothing.
  Neither helps; following a linked list needs the data itself to know
  where to go next.
- **Pollution.** `conflict` loses about 4% with either prefetcher: lines
  fetched ahead push out lines that were about to be used again.
- **Instructions.** `code_walk` is straight-line code two and a half times
  the size of the instruction cache. Without prefetching it misses once per
  line, 2,432 times, at IPC 0.40. Next-line prefetching leaves 30 misses and
  is 98.8% accurate, but every prefetch is late for the same reason as
  above (eight instructions take eight cycles, a line takes 12), so IPC
  reaches 0.60 rather than the ideal machine's 1.00.
- **Where the machine stands.** With caches, the predictor and the stride
  prefetcher, the compute-bound and streaming kernels run between 0.6 and
  1.0 instructions per cycle on a memory that is ten cycles away. What is
  left is what an in-order, single-issue pipeline cannot do: keep working
  while a miss is outstanding (`miss_indep`, `list_walk`), and execute more
  than one instruction per cycle (`ilp_chains`). Those are Phases 6 and 7.

### Phase 6: out-of-order execution

`ooo` is the out-of-order core with a 16-entry reorder buffer on exactly the
`pf-stride` machine: same caches, predictor and prefetchers, only the core
differs. `ooo-32` doubles the reorder buffer, reservation stations and
load/store queue.

- **Most kernels do not change, and should not.** This core dispatches and
  retires one instruction per cycle, so it cannot average more than one
  instruction per cycle, and the in-order pipeline already reaches that
  whenever nothing stalls it. `dep_chain`, `ilp_chains`, `loop_nest`,
  `matadd`, `array_sum`, `stride_walk`, `code_walk` and `bsearch` are all
  within 1%. Out-of-order execution by itself removes waiting, not work.
- **It gains where the instructions behind a waiting one do not depend on it.**
  `miss_indep` is built for this: every iteration has one load that misses
  and eight instructions that do not depend on it. The in-order pipeline
  stops dead for each miss; the out-of-order core keeps dispatching and
  executing the independent work behind the load until its reorder buffer
  is full. It needs 26% fewer cycles (51,836 down to 38,242; IPC 0.59 to
  0.79), and 29% fewer with the 32-entry buffer.
- **`list_walk` gains 22%** although its misses cannot be overlapped (each
  load's address comes from the load before): the instructions around each
  hop get done during the miss.
- **Load-use stalls are gone**, worth about 3% on `memcpy` and `sieve`.
- **Mispredictions cost more.** `branch_alt` and `calls` are 6% slower. Both
  mispredict often, and this core recovers only when the mispredicted branch
  retires. The numbers put the price at one extra cycle per misprediction:
  1,252 extra cycles for 1,253 mispredictions in `branch_alt`, 1,609 for
  1,612 in `calls`. Recovering when the branch executes would remove it, at
  the cost of restoring the rename table mid-flight.
- **A bigger window is not automatically better.** With 32 entries
  `array_sum` is 12% slower than with 16. The counters show why: the loads
  run further ahead of the prefetcher and compete with it for the single
  transfer the cache can have in progress, so real misses double (752 to
  1,596) and prefetches fall (3,897 to 3,047).
- **What is still missing.** `ilp_chains` has four independent chains and
  runs at exactly the same speed as `dep_chain`, which has one. The core can
  find the parallelism but has only one ALU, one bus and one-wide dispatch
  and retirement to use it with. That is Phase 7.

### Phase 7: two instructions per cycle

`ss` is the out-of-order core at width 2 on exactly the `ooo-32` machine:
same window, caches, predictor and prefetchers, the same RTL with one
parameter changed. The "Superscalar execution" table below puts the two side
by side.

![Three cores on the same machine](img/cores.svg)

- **This is the first configuration that goes above one instruction per
  cycle**, and eight of the fifteen kernels do: `ilp_chains` 1.79,
  `conflict` 1.50, `loop_nest` 1.30, `matadd` 1.24, `branch_alt` 1.15,
  `dep_chain` and `calls` 1.12, `miss_indep` 1.10.
- **Width pays for independent work and for nothing else.** `ilp_chains` and
  `dep_chain` are twins: the same instructions, the same number of them,
  1,500 iterations of 18. One arranges its 16 operations as four independent
  chains, the other as a single chain. At width 1 they take the same time to
  within 25 cycles. At width 2 `ilp_chains` needs 44% fewer cycles (27,066
  down to 15,071) and `dep_chain` 11% fewer (27,041 down to 24,041).
- **`dep_chain` runs exactly at its dependency limit.** Each of the 16
  operations needs the result of the one before, so an iteration cannot take
  fewer than 16 cycles however wide the core is. It takes 16.03. The loop
  counter and the branch are the only instructions that can run beside the
  chain, which is where 18 / 16 = 1.12 comes from. No further width would
  help; DDCA lecture 15a makes the same point with a pair of six-instruction
  examples, one reaching IPC 2 and one held to 1.2 by its dependences.
- **`ilp_chains` is limited by fetch, not by execution.** Its chains would
  allow an iteration every 4 cycles and a 2-wide core every 9. It takes 10.
  The counters say why: 15,017 fetches, 10 per iteration. The loop happens
  to start at an odd instruction position in its cache line, so one pair
  would straddle the line boundary and is split, and the closing branch ends
  up alone. One `nop` in front of the loop moves it: 13,518 fetches, 13,574
  cycles, IPC 1.99. The kernel is left as it is, because where code lies
  mattering this much is itself worth seeing.
- **Kernels that wait for memory do not move.** `code_walk` takes exactly
  the same 31,957 cycles: it waits for instruction cache lines, and a wider
  core waits just as long. `stride_walk`, `list_walk` and `memcpy` are
  within 3%. There is still one data cache port and one miss in progress at
  a time, and a store still retires alone.
- **Ordinary loops gain 19% to 38%:** `conflict` 38%, `calls` 29%,
  `branch_alt` 28%, `loop_nest` and `miss_indep` 25%, `matadd` 22%,
  `bsearch` 20%, `sieve` 19%. Their loop counters, address arithmetic and
  compares are small independent chains that a second slot can pick up.
- **Prediction is untouched.** Every kernel has exactly the same number of
  mispredictions at both widths. The predictor is trained from retired
  instructions in program order, so it sees the same history.
- **`array_sum` gets back what the larger window lost** in Phase 6 (61,412
  cycles against 69,582 for `ooo-32` and 62,064 for the 16-entry core).

### All phases together

The "Step by step" table below divides the journey into four steps, each
measured as cycles before over cycles after on the same fifteen kernels,
with the geometric mean in the last row.

| Step | Typical gain | Largest | Smallest |
| --- | ---: | ---: | ---: |
| Caches in front of a 10-cycle memory | 6.0x | 10.5x (`dep_chain`) | 2.8x (`conflict`, which thrashes) |
| A second way, a branch predictor, prefetchers | 1.5x | 4.5x (`conflict`) | 1.1x (`list_walk`) |
| Out-of-order execution, one wide | 1.03x | 1.4x (`miss_indep`) | 0.89x (`array_sum`) |
| Two wide | 1.24x | 1.8x (`ilp_chains`) | 1.0x (`code_walk`) |
| All together | 11.8x | 20.9x (`ilp_chains`) | 4.5x (`stride_walk`) |

Two things stand out. The memory system is worth far more than anything done
to the core: a factor of six from caches alone, before any cleverness. And
out-of-order execution on its own buys almost nothing on average, 3%, yet it
is what gives the second slot something to do: a two-wide core that
executes in order stops at the first instruction that has to wait, while
this one looks past it for work that is ready. The two together are worth
1.28x over the in-order pipeline on the same machine.

![All steps together, kernel by kernel](img/speedup_kernels.svg)

### Phase 8: cache policies

Phases 1 to 7 above added components. From here on the question is a
different one: for a component that is already there, which of the ways of
building it is better, and when. The cache is the first to be opened up. The
"Cache policies" tables below compare four replacement policies and eight
ways of handling stores on one machine: the in-order core, the predictor of
`bp2`, no prefetchers, and a 1 KiB data cache arranged as 8 sets of 4 ways
(two ways leave a replacement policy almost nothing to decide). The four
kernels written for this phase are measured here only; the fifteen earlier
ones are measured here as well.

Every run in these tables passed three checks, not one: the commit trace and
the final contents of memory equal the golden model's, and the cache's event
log agrees with the cache model, so the misses counted are the misses the
named policy should have.

**Replacement.**

![Replacement policy](img/policy_repl.svg)

- **No policy is best everywhere, and the differences are concentrated in a
  few kernels.** Twelve of the nineteen kernels miss exactly as often under
  all four policies: they stream through memory, and whatever is evicted is
  not needed again. Over all nineteen, the geometric mean of the cycle count
  relative to LRU is 1.03 for FIFO, 1.00 for random and 1.00 for pseudo-LRU.
  Lecture 22 says the same of LRU and random: on average, similar.
- **`lru_fifo` is the case where LRU is clearly better.** Three lines are used every
  round and a fourth place is shared by lines used once each. LRU misses
  only on those (25.4% of accesses; the optimal policy, computed in Phase
  10, saves 3 misses in the whole run). FIFO evicts
  the three busy lines in turn because they have been in longest: 62.7%, and
  1.73 times the cycles. Random does little better (57.3%).
- **`ways_plus_one` is where LRU is the worst possible choice.** Five lines
  are used in turn in a set that holds four. LRU always evicts exactly the
  line that is needed next, and so does FIFO: 4,005 misses in 4,005
  accesses. Random misses 40.6% and needs 0.60 of the cycles. The 40% can be
  worked out: the line random throws out is equally likely to be needed 1,
  2, 3 or 4 accesses later, so a miss comes every 2.5 accesses on average.
  This is the example of lecture 22 (set thrashing), measured.
- **Pseudo-LRU behaves like LRU here** (identical on `lru_fifo`, one miss
  different on `ways_plus_one`), with 3 bits per set instead of an ordering
  of four lines. It is not always a slightly worse LRU either: on `bsearch`
  it misses less than LRU (636 against 728), and random beats LRU on
  `sieve` (727 against 898). LRU is a guess about the future like the
  others.

**Stores.** `lru` in these tables is write-back with allocation and no
write buffer, the cache of Phase 3.

![Stores: write policies and the write buffer](img/policy_write.svg)

- **Write-through without a write buffer is slower wherever there are
  stores**, by up to a factor of two: every store waits for memory.
  `store_hot` shows it undiluted. Its 3,072 increments go to two lines that
  never leave the cache. Write-back sends nothing to memory at all;
  write-through sends 3,328 writes and takes 1.99 times as long.
- **A write buffer removes most of that, and merging removes the rest.**
  With four entries `store_hot` is at 1.10, with stores waiting for a full
  buffer in 9% of the cycles; with merging at 1.00. On `fill` the same three
  steps are 1.76, 1.42 and 0.85.
- **A write buffer helps write-back too**, although write-back sends only
  whole dirty lines: a miss no longer waits for its victim to be written
  before asking for the line it wants. `wb-buf` is never slower than `lru`
  and up to 28% faster (`fill` 0.72, `memcpy` 0.83, `sieve` 0.85).
- **Not allocating is slower than allocating, until the buffer merges.**
  `fill` writes a 16 KiB array from end to end. Allocating reads all 1,026
  lines from memory only to overwrite them. Not allocating reads 3, but
  sends each of the 4,096 stores to memory by itself, four writes where
  allocation needed one read and one write, and the stores wait for the
  buffer in 63% of the cycles: 1.14, slower than the base. With merging the
  four stores of a line mostly leave as one write (1,492 writes) and nothing
  waits: 0.42, less than half the cycles of the base. Lecture 22 lists both
  halves of this: allocation "can combine writes", and a block that is
  written completely need not be fetched. The measurement shows that the
  second only pays once something else does the combining.
- **The two best columns differ only in what a store that hits does**, and
  are within 0.2% of each other on every kernel except `matadd`: once
  stores that miss go round the cache through a merging buffer, most of the
  traffic is decided. Over all nineteen kernels they need 0.88 and
  0.89 of the base's cycles (geometric mean), against 0.95 for a write
  buffer alone and 1.16 for write-through without one.
- **The number of writes and the time they cost are different things.** `store_hot` under `wt-merge`
  still sends 3,013 writes to memory, three thousand more than write-back,
  and takes the same time: memory keeps up, and the processor is never made
  to wait. What costs cycles is waiting, not the number of transfers. (On a
  machine where the memory port is shared the transfers would matter too.
  Phase 9 shares it between two caches; sharing it between cores is future
  work.)

Two limits of these numbers. The write buffer is never read from: a load of
a line with a queued write waits for memory to have it, where a real design
would forward. And memory here accepts a write of one byte as readily as a
write of a whole line, so the cost of write-through is the waiting alone.

### Phase 9: a second cache level

The tables from "Victim buffer" on, below. Memory is made five times slower (50
cycles per line instead of 10), which is what makes a second level worth
having, and the machine is otherwise that of the policy tables: the in-order
core, the predictor of `bp2`, a 1 KiB direct-mapped instruction cache and a
1 KiB data cache of 8 sets x 4 ways. `h-l1` has those two caches and
nothing below them. `h-l2` adds an 8 KiB second-level cache (L2) that both
share and that answers in 4 cycles. `h-incl` makes it inclusive, `h-vb` adds
a 4-entry victim buffer beside the data cache, and `h-pf` adds the
prefetchers of `pf-stride`. All 21 kernels run on all five.

![A second cache level in front of a slow memory](img/hierarchy.svg)

**What the second level buys.**

- **A third to a quarter of the cycles, where the data fits.** Over all 21
  kernels `h-l2` needs 0.57 of the cycles of `h-l1` (geometric mean). The
  kernels whose working set fits in 8 KiB but not in 1 KiB gain most:
  `list_walk` 0.30, `memcpy` 0.30, `sieve` 0.34, `matadd` 0.38, and
  `code_walk` 0.26, where it is instructions that the first level cannot
  hold. Seven kernels fit in the first level and do not change.
- **It costs where nothing fits.** `array_sum` (16 KiB) and `stride_walk`
  (32 KiB) are slower with the L2 than without: 1.10 and 1.11. Every line
  they touch is in neither cache, and the L2 is asked first: 58 cycles
  instead of 52. That is the price of asking the levels one after the
  other, which lecture 21 puts as "slower access to data if previous level
  misses".
- **The staircase.** `ws_walk` is the same loop over working sets from 256
  bytes to 32 KiB. A load costs 1 cycle while the working set fits the data
  cache, 7 while it fits the L2, and 59 after that; without the L2 it goes
  straight from 1 to 53. The steps are exactly at 1 KiB and at 8 KiB. That
  the 8 KiB working set still fits an 8 KiB cache which also has to hold the
  program is not an accident: the loop's instructions stay in the
  instruction cache, the L2 is not asked for them again, and it is free to
  let them go. (An inclusive L2 is not. See below.)
- **The two caches hardly ever get in each other's way.** Sharing one L2
  means waiting when both miss at once. On this core that is at most 0.3% of
  the cycles of any kernel: a pipeline that is waiting for data is not
  fetching.

![The staircase](img/staircase.svg)

**Measured against the formula.** Lecture 21 gives the time per access of a
hierarchy as `T1 = t1 + m1 x T2` with `T2 = t2 + m2 x T3`, each miss rate
counted over what the level above passed down.

- **For loads it is exact.** On the staircase the formula and the
  measurement agree to two decimal places at every size (1.00, 7.00,
  59.00). The inputs are the two penalties of this design, 6 cycles for a
  miss that the L2 serves and 52 more for one that it does not, and the
  hierarchy testbench checks both directly.
- **For stores it leaves out the write-backs.** The formula counts what an
  access waits for its own line. A miss that first has to write a dirty
  victim down a level waits for that too. On `fill` the formula gives 15.5
  cycles per access and the machine takes 26.3; on `miss_indep` 21.9 and
  30.5. Adding the write-backs the counters report (5 cycles for each dirty
  line the data cache hands to the L2, 51 for each the L2 writes to memory)
  brings every kernel within 0.11 of a cycle, and most within 0.04.
- **The miss rate has to be the right one.** The L2 is also sent the dirty
  lines of the data cache, which nearly always hit. Counting those requests
  in its miss rate, as a first version of this table did, makes the L2 look
  twice as good as it is on `fill` (51% instead of 100%) and the formula
  correspondingly wrong. The lecture's definition, over "only the
  references that missed" at the level above, is the one that works.

**Inclusion.**

- **Mostly it costs nothing.** On 13 of the 21 kernels the inclusive L2
  never has to take a line back, and `h-incl` takes exactly the cycles of
  `h-l2`.
- **Where it costs, it removes a line that is in constant use.** The L2 chooses what to evict from
  what it has been asked, and it is only asked on a first-level miss. A line
  that the first level serves in every cycle is, to the L2, a line nobody
  has wanted for a long time. `incl_victim` is built on that: a counter used
  in every step, and four buffers that share its set in the L2. Without
  inclusion the L2 serves every miss after the first round (2,011 lines
  asked for, 10 missed). With inclusion it keeps evicting the counter, which
  takes it out of the data cache too; the counter comes back from memory and
  pushes out a buffer that is needed next: 2,511 asked for, 2,508 missed,
  504 lines taken back. The inclusive machine takes 6.1 times the cycles of
  the non-inclusive one, and 1.58 times the cycles of the machine with no L2
  at all.
- **It was not only the kernel built for it.** `lru_fifo`, written in Phase
  8 to compare replacement policies, has three hot lines and takes 2.3 times
  the cycles under inclusion (181 lines taken back, L2 misses up from 53 to
  944).
- **In ordinary programs the line removed is often the program's own code.** `array_sum`, `stride_walk`,
  `fill` and `list_walk` stream data through the L2 while their loop sits
  in the instruction cache. The data pushes the loop's own lines out of the
  L2, and inclusion then removes them from under the fetch: 38 instruction
  lines taken back in `array_sum`, 40 in `list_walk`, and 2 to 16% more
  cycles. The staircase shows the same thing in its cleanest form. At 8 KiB
  the working set and the loop no longer both fit the inclusive L2, and a
  load costs 8.69 cycles instead of 7.00. Beyond that every eviction first
  spends two cycles asking the first level for a line it does not have: 61
  instead of 59.
- Over all 21 kernels inclusion costs 15% (geometric mean of `h-incl`
  against `h-l2`). What it is for, a single place to look when another core
  asks about a line, does not exist on this machine: it has one core
  (several are future work, HISTORY.md section 6).

**The victim buffer.**

- **Beside a direct-mapped cache it removes the ping-pong, not the lack of
  room.** `conflict` reads two arrays that collide line for line in a
  direct-mapped cache: 7,808 misses in 7,808 accesses on `dm`. With four
  lines beside the cache (`dm-vb`) there are still 7,808 misses, but 6,816
  of them are served by the buffer in one cycle each, and the kernel takes
  0.42 of the cycles. A second way (`2way`) takes 0.27, because it holds all
  32 lines and the buffer holds 4: once per pass each pair of lines still
  has to come from memory. Over the fifteen kernels of the first stage the
  buffer is worth 0.94 and the second way 0.92.
- **Beside a 4-way cache it removes LRU's worst case.** `ways_plus_one` uses
  five lines in turn in a set of four; LRU misses every time, and in Phase 8
  only random replacement did better. With a victim buffer the fifth line is
  simply in the buffer: 4,000 of the 4,005 misses are swaps, the L2 is asked
  nine times in the whole run, and the kernel needs 0.59 of the cycles of
  `h-l2`. `incl_victim` is the same picture (0.68).
- **It does nothing else.** Sixteen of the 21 kernels take exactly the same
  cycles with and without it. A victim buffer answers conflicts; a kernel
  that misses because its data is large gets nothing from it.

**Prefetchers above an L2.**

- **They do what they did before, on the misses that are left.** With
  prefetching the L2 is asked ahead of time: `code_walk` goes from 0.26 to
  0.16 of `h-l1`, `ws_walk` from 0.27 to 0.18, `memcpy` from 0.30 to 0.21.
  `array_sum` and `stride_walk`, which the L2 made slower, are helped back
  (0.95 and 1.03), though a prefetch that has to go all the way to memory
  takes long: a third of the prefetched lines that were used arrived after
  they were wanted.
- **In a set that is already too full they do harm.** `lru_fifo` takes 4.4
  times the cycles of `h-l2` with the prefetchers on, and `incl_victim` 6.6
  times. In both, every line the kernel uses falls into one set. The stride
  prefetcher recognises the stride and fetches the line four steps ahead,
  into that same set, where it evicts a line that is needed sooner; by the
  time the prefetched line is wanted it has been evicted in turn. In
  `lru_fifo` 1,887 prefetches were issued and none was used, and the data
  cache's misses went from 1,958 to 7,529. Without these two kernels the
  prefetchers are worth 0.90 over the rest; with them, the mean is worse
  than no prefetching (1.06).

Limits of these numbers. The L2 serves one request at a time, so a fetch
and a data access that miss together are served one after the other. A
write of a whole line that misses in the L2 reads the line from memory
before overwriting it; that happened six times in all 21 kernels together,
so it does not show. And the memory here takes the same 50 cycles for every
line, whether the previous request was for its neighbour or not.

### Phase 10: the cache's parameters, swept

Phases 8 and 9 compared a handful of machines. This phase moves each
parameter across its range, one at a time. The tables and charts are in
[SWEEPS.md](SWEEPS.md), which one command writes
(`python tools/regress.py sweep`); this section says, for each sweep, what
it shows and where the textbook expectation did not hold.

Two engines produce the numbers. Misses come from the cache model replaying
each kernel's data accesses, which takes milliseconds per configuration and
can also do what hardware cannot: evict the line that will be used furthest
in the future, the optimal policy. Cycles come from the RTL, on a grid of 43
machines of the in-order core. The two are held together: in each of the 903
RTL runs the model, run by itself from the golden model's accesses, must
reproduce the RTL's cache counters exactly, and does (VERIFICATION.md
section 5). The data-side sweeps use the 15 kernels with at least 1,000 data
accesses, and "all together" means every data access of those kernels in one
count.

**Cache size and associativity.**

![Miss rate against cache size](img/sweep_size.svg)

- **What it shows.** All together, the miss rate falls from 50.9% in a
  256-byte direct-mapped data cache to 9.7% in 16 KiB. Up to 2 KiB a second
  way is worth more than twice the size: 1 KiB in two ways misses 34.6% of
  the time, 2 KiB direct-mapped 40.0%, and in cycles the same pair is 0.85
  against 0.90 of the 1 KiB direct-mapped machine. Beyond two ways there is
  little left: from 1 KiB on, two ways and four ways take the same cycles to
  two decimal places.
- **More ways are not always fewer misses.** At the same size, nine of the
  fifteen kernels have at least one step from one associativity to the next
  that adds misses. `ways_plus_one` at 1 KiB misses 1,605 times
  direct-mapped, 2,405 times in two ways and 4,005 times (every access) in
  four. All together, an 8 KiB cache misses more often in four ways (17.2%)
  than direct-mapped (16.1%). The same bytes in more ways are fewer sets,
  and lines that each had a set of their own now share one.
- **A cache as large as the data can still miss every time.** `stride_walk`
  touches 512 lines, 16 KiB, over and over. In a 16 KiB cache it misses on
  all 7,680 accesses with one, two, four and eight ways; fully associative
  it misses 512 times, once per line. It reads one value every 64 bytes, so
  it only ever uses every second set, and half of the cache is out of its
  reach at any associativity short of full. That one kernel is the whole
  gap between 9.7% and 2.9% in the chart's last column.

![Three results that go against the rule of thumb](img/anomalies.svg)

**Line size.**

- **What it shows.** A kernel that walks through memory halves its misses
  each time the line doubles: `array_sum` misses on 100%, 50%, 25%, 12.5%
  and 6.2% of its accesses with lines of 8 to 128 bytes in a 1 KiB cache.
  All together the miss rate goes from 68.1% to 19.3%, and the cycles from
  1.44 to 0.78 of the 32-byte machine.
- **The miss rate never rises again as the line grows.** Lecture 22 draws hit rate against block size
  rising and then falling: a line that is too large leaves too few lines.
  Here no kernel misses more with a larger line, even at 128 bytes, when the
  cache is eight lines in two sets. That says more about the experiment than
  about the lecture. These kernels either stream, and then a bigger line is
  always better, or live in a handful of lines, and then it makes no
  difference (`lru_fifo`, `incl_victim` and `ways_plus_one` are flat). And
  memory here takes 10 cycles for a line of any size, so the larger
  transfer is free. `list_walk` shows the flat part of the curve: 50% from
  16 bytes to 64. Each hop reads two fields that lie within 16 bytes, so
  from there on a hop is one miss, and whatever else a larger line brings is
  not the node the list visits next.

**Replacement policy against the optimal one.**

![Replacement policies against the optimal one](img/sweep_repl.svg)

- **What it shows.** In 8 sets of 4 ways, the four real policies miss
  between 1.22 and 1.37 times as often as the optimal one, all kernels
  together (LRU 1.25, FIFO 1.37, random 1.22, pseudo-LRU 1.23). The gap
  widens with the cache: LRU is at 1.21 times the optimum in 512 bytes and
  1.45 in 8 KiB, because a larger cache has more to decide. Where nothing is
  reused, knowing the future does not help either: the optimal policy saves
  `array_sum` 216 of 4,608 misses. And on `lru_fifo`, the kernel written to
  show LRU at its best, LRU is within three misses of the optimum (1,958
  against 1,955).
- **LRU is not the best real policy on these kernels.** Random replacement
  misses less often than LRU at every size from 512 bytes to 8 KiB, and so
  does pseudo-LRU. At small sizes that is the work of two kernels built as
  LRU's worst case (`ways_plus_one` and `incl_victim`); without them LRU
  leads, 1.11 against 1.19 at 1 KiB. At 4 and 8 KiB random leads even
  without them (1.24 against 1.32, and 1.25 against 1.36): a loop that
  sweeps round data a little larger than the cache is the same worst case
  on a larger scale, since the line used longest ago is exactly the one the
  sweep reaches next. Lecture 22 says LRU and random are similar on average;
  on small loops the average is made of cases like these.

**Three kinds of miss.**

![Three kinds of miss](img/sweep_classes.svg)

- **What it shows.** With the definitions of lecture 22, the kernels sort
  themselves the way they were built. `conflict` is 7,776 conflict misses
  out of 7,808 in a direct-mapped cache and none in two ways; `store_hot`
  goes from 1,148 to none the same way. The streaming kernels are capacity
  misses (`stride_walk`: 6,720 of 7,680), which no associativity touches.
- **Conflict misses can grow with associativity.** `ways_plus_one` has
  1,600 conflict misses direct-mapped, 2,400 in two ways and 4,000 in four.
  It is the same effect as above, seen through the classification.
- **Some "conflict" misses are not conflicts.** `array_sum` walks straight
  through memory and has 256 conflict misses at every associativity. They
  are what LRU loses to the optimal policy: the definition measures capacity
  with optimal replacement, so everything a real policy gets wrong lands in
  the conflict class. (The plan for this phase first had LRU in that
  definition; with LRU the 256 would have been called capacity.)

**Write policy.**

- **What it shows.** With a 4-entry write buffer that does not merge,
  write-back with allocation, the cache of Phase 3, is the best of the four
  combinations on average: write-through with allocation needs 1.16 times
  the cycles, and not allocating 1.08 (write-back) or 1.09
  (write-through), over the 15 kernels.
- **Not allocating loses on the kernel it is meant for.** `fill` writes an
  array from end to end. Without allocation it reads 3 lines from memory
  instead of 1,026, and takes 1.57 times the cycles, because 4,096 separate
  stores go to memory where allocation sent 994 lines. Phase 8 found this
  with its own configurations; here it is on the plain two-by-two.
- **Write-through can be faster than write-back.** On `matadd` it needs
  0.96 of the cycles (0.94 without allocation) although it sends four times
  as many writes to memory, 2,560 against 640. The counters say where: the
  data cache keeps the core waiting for 23,461 cycles instead of 25,742.
  They do not say why, and it was not investigated further.

**Second-level cache: size and inclusion.**

![Cycles against second-level cache size](img/sweep_l2.svg)

- **What it shows.** A second level only pays once it is larger than the
  first. At 1 KiB, the size of each first-level cache, it is a loss (1.06 of
  the cycles of the machine without it, all 21 kernels); at 2 KiB 0.84, at
  8 KiB 0.57 (the machine of Phase 9), at 32 KiB 0.47. Kernel by kernel it
  is a step at the size where the working set fits: `array_sum` is at 1.10
  up to 8 KiB and at 0.33 from 16 KiB.
- **The cost of inclusion is not a constant.** It is 12% to 15% from 1 KiB
  to 8 KiB and gone above that: 1.01 at 16 KiB and 1.00 at 32 KiB, with 46
  and 16 lines taken back in all 21 kernels together, against 845 at 8 KiB
  and 16,111 at 1 KiB. Inclusion costs when the second level is small
  enough to want back lines the first level is still using. The 15% of
  Phase 9 was a property of the 8 KiB chosen there.
- **At 1 KiB the inclusive cache is clearly the faster one on five
  kernels**, all of which store: `fill` 1.11 against 1.51, `sieve` 1.06 against 1.44,
  `memcpy` 1.14 against 1.37, `matadd` 1.17 against 1.29, `miss_indep` 1.17
  against 1.27. A non-inclusive second level that small has usually evicted
  a line by the time the data cache writes it back. The write then misses,
  and this cache reads the line from memory before overwriting all of it:
  5,562 times at 1 KiB in the 21 kernels, against once under inclusion,
  where the line is by definition still there. Phase 9 listed that read as
  a limit that did not show, six times in all at 8 KiB. At 1 KiB it
  decides the comparison.

**Instruction cache.** Every kernel but one has between 3 and 9 lines of
code, so the size of the instruction cache only matters to `code_walk`,
which misses 2,431 times until the cache holds its 82 lines. Fetches down
mispredicted paths add one or two misses to that. Nothing here was
unexpected.

Limits of these numbers. The sweeps run on the in-order core only. The
kernels are small and several were written to provoke one effect, so "all
together" is a sum over this set of programs, not an estimate for any
other. Memory takes the same time for a line of any size. And the model's
misses are for a cache without prefetchers or a victim buffer.

### Where each component helped most

Collected from all the phases above: each component on the kernel written
to show what it is for, against the same machine without it.

![Where each component helped most](img/gains.svg)

### Where a component did not help

The same collection for the limits: each bar is one component on one
kernel, against the same machine without it.

![Where a component did not help](img/no_help.svg)

Every one of these was measured because a kernel had been written to look
for it, or turned up in a kernel written for something else. The two at the
bottom are the stride prefetcher in a set that is already too full (Phase
9); the smallest, a wider core on a kernel that only waits for instructions
(Phase 7). None is a defect in the component. Each is the component doing
what it was built to do on a workload that does not reward it.

## Measurements

The tables behind everything above. They are generated: every number comes
from the counters of one run of the bench stage.

<!-- BEGIN GENERATED -->

### Configurations

| Name | Core | Memory latency (cycles per line) | Instruction cache | Data cache | Branch predictor | Prefetch (instructions / data) |
| --- | --- | ---: | --- | --- | --- | --- |
| `ideal`: Ideal memory | in-order | 0 | none | none | none (always not taken) | none / none |
| `nocache`: No caches | in-order | 10 | none | none | none (always not taken) | none / none |
| `dm`: 1 KiB direct-mapped caches | in-order | 10 | 1024 bytes, direct-mapped | 1024 bytes, direct-mapped | none (always not taken) | none / none |
| `2way`: D cache 2-way | in-order | 10 | 1024 bytes, direct-mapped | 1024 bytes, 2-way | none (always not taken) | none / none |
| `bp1`: Last-time predictor | in-order | 10 | 1024 bytes, direct-mapped | 1024 bytes, 2-way | 64-entry BTB, last outcome | none / none |
| `bp2`: 2-bit predictor | in-order | 10 | 1024 bytes, direct-mapped | 1024 bytes, 2-way | 64-entry BTB, 2-bit counters | none / none |
| `pf-next`: Next-line prefetch | in-order | 10 | 1024 bytes, direct-mapped | 1024 bytes, 2-way | 64-entry BTB, 2-bit counters | next line / next line |
| `pf-stride`: Stride prefetch for data | in-order | 10 | 1024 bytes, direct-mapped | 1024 bytes, 2-way | 64-entry BTB, 2-bit counters | next line / stride |
| `ooo`: Out-of-order, 16-entry reorder buffer | out-of-order, 1 wide: ROB 16, 8 stations, LSQ 8 | 10 | 1024 bytes, direct-mapped | 1024 bytes, 2-way | 64-entry BTB, 2-bit counters | next line / stride |
| `ooo-32`: Out-of-order, 32-entry reorder buffer | out-of-order, 1 wide: ROB 32, 16 stations, LSQ 16 | 10 | 1024 bytes, direct-mapped | 1024 bytes, 2-way | 64-entry BTB, 2-bit counters | next line / stride |
| `ss`: Superscalar: two wide, 32-entry reorder buffer | out-of-order, 2 wide: ROB 32, 16 stations, LSQ 16 | 10 | 1024 bytes, direct-mapped | 1024 bytes, 2-way | 64-entry BTB, 2-bit counters | next line / stride |

### Cycles

| Kernel | Instructions | `ideal` | `nocache` | `dm` | `2way` | `bp1` | `bp2` | `pf-next` | `pf-stride` | `ooo` | `ooo-32` | `ss` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `array_sum` | 46116 | 55332 | 599416 | 116320 | 116320 | 107148 | 107134 | 65689 | 61981 | 62064 | 69582 | 61412 |
| `branch_alt` | 18771 | 28773 | 266473 | 28821 | 28821 | 23827 | 21329 | 21304 | 21304 | 22556 | 22556 | 16304 |
| `bsearch` | 23885 | 34022 | 315045 | 43318 | 43222 | 38532 | 38018 | 36312 | 36977 | 36807 | 36977 | 29610 |
| `calls` | 21615 | 43217 | 364157 | 43272 | 43272 | 25698 | 25698 | 25690 | 25690 | 27299 | 27299 | 19294 |
| `code_walk` | 19269 | 19331 | 212311 | 48503 | 48503 | 48455 | 48455 | 31957 | 31957 | 31957 | 31957 | 31957 |
| `conflict` | 27590 | 35398 | 365934 | 130550 | 35830 | 28274 | 28156 | 29217 | 29234 | 29965 | 29680 | 18394 |
| `dep_chain` | 27009 | 30011 | 315091 | 30059 | 30059 | 27065 | 27065 | 27039 | 27039 | 27041 | 27041 | 24041 |
| `ilp_chains` | 27024 | 30026 | 315256 | 30086 | 30086 | 27092 | 27092 | 27064 | 27064 | 27066 | 27066 | 15071 |
| `list_walk` | 24418 | 34146 | 321828 | 77683 | 77131 | 70535 | 70513 | 82304 | 70463 | 55107 | 55107 | 53736 |
| `loop_nest` | 18914 | 28132 | 263342 | 28168 | 28168 | 19582 | 19274 | 19255 | 19255 | 19415 | 19415 | 14520 |
| `matadd` | 35554 | 40928 | 439706 | 68780 | 68780 | 64286 | 63988 | 36767 | 36793 | 36857 | 36881 | 28635 |
| `memcpy` | 26163 | 41009 | 368657 | 82893 | 82893 | 72703 | 72689 | 44272 | 44374 | 42960 | 42961 | 42296 |
| `miss_indep` | 30355 | 34979 | 360925 | 60388 | 60388 | 56976 | 56976 | 52202 | 51836 | 38242 | 36854 | 27493 |
| `sieve` | 23886 | 39943 | 357418 | 59171 | 59643 | 46949 | 46299 | 37867 | 38405 | 37102 | 37114 | 30209 |
| `stride_walk` | 30775 | 53303 | 446025 | 151107 | 151107 | 135815 | 135789 | 197657 | 98430 | 98385 | 98385 | 98236 |

### Instructions per cycle

| Kernel | Instructions | `ideal` | `nocache` | `dm` | `2way` | `bp1` | `bp2` | `pf-next` | `pf-stride` | `ooo` | `ooo-32` | `ss` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `array_sum` | 46116 | 0.83 | 0.08 | 0.40 | 0.40 | 0.43 | 0.43 | 0.70 | 0.74 | 0.74 | 0.66 | 0.75 |
| `branch_alt` | 18771 | 0.65 | 0.07 | 0.65 | 0.65 | 0.79 | 0.88 | 0.88 | 0.88 | 0.83 | 0.83 | 1.15 |
| `bsearch` | 23885 | 0.70 | 0.08 | 0.55 | 0.55 | 0.62 | 0.63 | 0.66 | 0.65 | 0.65 | 0.65 | 0.81 |
| `calls` | 21615 | 0.50 | 0.06 | 0.50 | 0.50 | 0.84 | 0.84 | 0.84 | 0.84 | 0.79 | 0.79 | 1.12 |
| `code_walk` | 19269 | 1.00 | 0.09 | 0.40 | 0.40 | 0.40 | 0.40 | 0.60 | 0.60 | 0.60 | 0.60 | 0.60 |
| `conflict` | 27590 | 0.78 | 0.08 | 0.21 | 0.77 | 0.98 | 0.98 | 0.94 | 0.94 | 0.92 | 0.93 | 1.50 |
| `dep_chain` | 27009 | 0.90 | 0.09 | 0.90 | 0.90 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.12 |
| `ilp_chains` | 27024 | 0.90 | 0.09 | 0.90 | 0.90 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.79 |
| `list_walk` | 24418 | 0.72 | 0.08 | 0.31 | 0.32 | 0.35 | 0.35 | 0.30 | 0.35 | 0.44 | 0.44 | 0.45 |
| `loop_nest` | 18914 | 0.67 | 0.07 | 0.67 | 0.67 | 0.97 | 0.98 | 0.98 | 0.98 | 0.97 | 0.97 | 1.30 |
| `matadd` | 35554 | 0.87 | 0.08 | 0.52 | 0.52 | 0.55 | 0.56 | 0.97 | 0.97 | 0.96 | 0.96 | 1.24 |
| `memcpy` | 26163 | 0.64 | 0.07 | 0.32 | 0.32 | 0.36 | 0.36 | 0.59 | 0.59 | 0.61 | 0.61 | 0.62 |
| `miss_indep` | 30355 | 0.87 | 0.08 | 0.50 | 0.50 | 0.53 | 0.53 | 0.58 | 0.59 | 0.79 | 0.82 | 1.10 |
| `sieve` | 23886 | 0.60 | 0.07 | 0.40 | 0.40 | 0.51 | 0.52 | 0.63 | 0.62 | 0.64 | 0.64 | 0.79 |
| `stride_walk` | 30775 | 0.58 | 0.07 | 0.20 | 0.20 | 0.23 | 0.23 | 0.16 | 0.31 | 0.31 | 0.31 | 0.31 |

### Branch prediction

`2way` has no predictor: every taken branch and every jump is a
misprediction. The others add a predictor to the same machine. A
misprediction costs two cycles.

| Kernel | Branches | Jumps | `2way` branches mispredicted | jumps mispredicted | `bp1` branches mispredicted | jumps mispredicted | `bp2` branches mispredicted | jumps mispredicted |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `array_sum` | 4617 | 1 | 4606 (99.8%) | 1 (100.0%) | 20 (0.4%) | 1 (100.0%) | 13 (0.3%) | 1 (100.0%) |
| `branch_alt` | 5003 | 1250 | 3749 (74.9%) | 1250 (100.0%) | 2501 (50.0%) | 1 (0.1%) | 1252 (25.0%) | 1 (0.1%) |
| `bsearch` | 5262 | 2004 | 1938 (36.8%) | 2004 (100.0%) | 1594 (30.3%) | 3 (0.1%) | 1337 (25.4%) | 3 (0.1%) |
| `calls` | 801 | 9601 | 799 (99.8%) | 9601 (100.0%) | 2 (0.2%) | 1611 (16.8%) | 2 (0.2%) | 1611 (16.8%) |
| `code_walk` | 31 | 1 | 29 (93.5%) | 1 (100.0%) | 2 (6.5%) | 1 (100.0%) | 2 (6.5%) | 1 (100.0%) |
| `conflict` | 3966 | 0 | 3902 (98.4%) | 0 (-) | 124 (3.1%) | 0 (-) | 65 (1.6%) | 0 (-) |
| `dep_chain` | 1501 | 1 | 1499 (99.9%) | 1 (100.0%) | 2 (0.1%) | 1 (100.0%) | 2 (0.1%) | 1 (100.0%) |
| `ilp_chains` | 1504 | 1 | 1499 (99.7%) | 1 (100.0%) | 2 (0.1%) | 1 (100.0%) | 2 (0.1%) | 1 (100.0%) |
| `list_walk` | 3354 | 1 | 3326 (99.2%) | 1 (100.0%) | 28 (0.8%) | 1 (100.0%) | 17 (0.5%) | 1 (100.0%) |
| `loop_nest` | 4766 | 0 | 4607 (96.7%) | 0 (-) | 314 (6.6%) | 0 (-) | 160 (3.4%) | 0 (-) |
| `matadd` | 2715 | 1 | 2557 (94.2%) | 1 (100.0%) | 310 (11.4%) | 1 (100.0%) | 161 (5.9%) | 1 (100.0%) |
| `memcpy` | 5641 | 1 | 5117 (90.7%) | 1 (100.0%) | 22 (0.4%) | 1 (100.0%) | 15 (0.3%) | 1 (100.0%) |
| `miss_indep` | 1717 | 1 | 1710 (99.6%) | 1 (100.0%) | 4 (0.2%) | 1 (100.0%) | 4 (0.2%) | 1 (100.0%) |
| `sieve` | 7355 | 1 | 7006 (95.3%) | 1 (100.0%) | 659 (9.0%) | 1 (100.0%) | 334 (4.5%) | 1 (100.0%) |
| `stride_walk` | 7695 | 0 | 7678 (99.8%) | 0 (-) | 32 (0.4%) | 0 (-) | 19 (0.2%) | 0 (-) |

### Data prefetching

`bp2` has no prefetcher. Misses are accesses that had to wait for
the data cache to fetch a line that was not on its way already.
Accuracy = prefetched lines that were used / prefetches issued.
Coverage = prefetched lines used / (those + remaining misses).
Timely = share of the used prefetches that arrived before they were
needed.

| Kernel | `bp2` misses | `pf-next` misses | issued | accuracy | coverage | timely | `pf-stride` misses | issued | accuracy | coverage | timely |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `array_sum` | 4608 | 9 | 4608 | 99.8% | 99.8% | 0.0% | 697 | 3945 | 99.1% | 84.9% | 100.0% |
| `bsearch` | 701 | 527 | 656 | 37.2% | 31.6% | 61.5% | 639 | 64 | 98.4% | 9.0% | 47.6% |
| `list_walk` | 3335 | 3115 | 3116 | 7.1% | 6.6% | 100.0% | 3335 | 0 | - | 0.0% | - |
| `matadd` | 1729 | 28 | 1729 | 98.4% | 98.4% | 99.5% | 29 | 1727 | 98.4% | 98.3% | 98.9% |
| `memcpy` | 2432 | 19 | 2432 | 99.2% | 99.2% | 53.1% | 19 | 2432 | 99.2% | 99.2% | 52.6% |
| `miss_indep` | 1640 | 1130 | 1625 | 33.5% | 32.5% | 6.1% | 1329 | 313 | 99.4% | 19.0% | 100.0% |
| `sieve` | 886 | 52 | 901 | 94.3% | 94.2% | 30.0% | 203 | 707 | 96.9% | 77.1% | 34.0% |
| `stride_walk` | 7680 | 7680 | 7680 | 0.0% | 0.0% | - | 3807 | 3918 | 98.9% | 50.4% | 97.4% |

### Instruction prefetching

`bp2` has no prefetcher. Misses are accesses that had to wait for
the instruction cache to fetch a line that was not on its way already.
Accuracy = prefetched lines that were used / prefetches issued.
Coverage = prefetched lines used / (those + remaining misses).
Timely = share of the used prefetches that arrived before they were
needed.

| Kernel | `bp2` misses | `pf-next` misses | issued | accuracy | coverage | timely |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `code_walk` | 2432 | 30 | 2431 | 98.8% | 98.8% | 0.0% |

### Out-of-order execution

`pf-stride` is the in-order core; the others are the out-of-order core
with the same caches, predictor and prefetchers. Cycles relative to
the in-order core are in brackets: below 1.00 is faster.

| Kernel | Instructions | `pf-stride` cycles | IPC | mispredictions | `ooo` cycles | IPC | mispredictions | `ooo-32` cycles | IPC | mispredictions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `array_sum` | 46116 | 61981 | 0.74 | 14 | 62064 (1.00) | 0.74 | 13 | 69582 (1.12) | 0.66 | 13 |
| `branch_alt` | 18771 | 21304 | 0.88 | 1253 | 22556 (1.06) | 0.83 | 1253 | 22556 (1.06) | 0.83 | 1253 |
| `bsearch` | 23885 | 36977 | 0.65 | 1340 | 36807 (1.00) | 0.65 | 1339 | 36977 (1.00) | 0.65 | 1339 |
| `calls` | 21615 | 25690 | 0.84 | 1613 | 27299 (1.06) | 0.79 | 1612 | 27299 (1.06) | 0.79 | 1612 |
| `code_walk` | 19269 | 31957 | 0.60 | 3 | 31957 (1.00) | 0.60 | 2 | 31957 (1.00) | 0.60 | 2 |
| `conflict` | 27590 | 29234 | 0.94 | 65 | 29965 (1.03) | 0.92 | 65 | 29680 (1.02) | 0.93 | 65 |
| `dep_chain` | 27009 | 27039 | 1.00 | 3 | 27041 (1.00) | 1.00 | 2 | 27041 (1.00) | 1.00 | 2 |
| `ilp_chains` | 27024 | 27064 | 1.00 | 3 | 27066 (1.00) | 1.00 | 2 | 27066 (1.00) | 1.00 | 2 |
| `list_walk` | 24418 | 70463 | 0.35 | 18 | 55107 (0.78) | 0.44 | 17 | 55107 (0.78) | 0.44 | 17 |
| `loop_nest` | 18914 | 19255 | 0.98 | 160 | 19415 (1.01) | 0.97 | 160 | 19415 (1.01) | 0.97 | 160 |
| `matadd` | 35554 | 36793 | 0.97 | 162 | 36857 (1.00) | 0.96 | 161 | 36881 (1.00) | 0.96 | 161 |
| `memcpy` | 26163 | 44374 | 0.59 | 16 | 42960 (0.97) | 0.61 | 15 | 42961 (0.97) | 0.61 | 15 |
| `miss_indep` | 30355 | 51836 | 0.59 | 5 | 38242 (0.74) | 0.79 | 4 | 36854 (0.71) | 0.82 | 4 |
| `sieve` | 23886 | 38405 | 0.62 | 335 | 37102 (0.97) | 0.64 | 334 | 37114 (0.97) | 0.64 | 334 |
| `stride_walk` | 30775 | 98430 | 0.31 | 20 | 98385 (1.00) | 0.31 | 19 | 98385 (1.00) | 0.31 | 19 |

### Superscalar execution

`ooo-32` handles one instruction per cycle; the others are the same
core and the same machine at two per cycle. Cycles relative to the
one-wide core are in brackets: below 1.00 is faster. An IPC above 1
is only possible at width 2.

| Kernel | Instructions | `ooo-32` cycles | IPC | mispredictions | `ss` cycles | IPC | mispredictions |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `array_sum` | 46116 | 69582 | 0.66 | 13 | 61412 (0.88) | 0.75 | 13 |
| `branch_alt` | 18771 | 22556 | 0.83 | 1253 | 16304 (0.72) | 1.15 | 1253 |
| `bsearch` | 23885 | 36977 | 0.65 | 1339 | 29610 (0.80) | 0.81 | 1339 |
| `calls` | 21615 | 27299 | 0.79 | 1612 | 19294 (0.71) | 1.12 | 1612 |
| `code_walk` | 19269 | 31957 | 0.60 | 2 | 31957 (1.00) | 0.60 | 2 |
| `conflict` | 27590 | 29680 | 0.93 | 65 | 18394 (0.62) | 1.50 | 65 |
| `dep_chain` | 27009 | 27041 | 1.00 | 2 | 24041 (0.89) | 1.12 | 2 |
| `ilp_chains` | 27024 | 27066 | 1.00 | 2 | 15071 (0.56) | 1.79 | 2 |
| `list_walk` | 24418 | 55107 | 0.44 | 17 | 53736 (0.98) | 0.45 | 17 |
| `loop_nest` | 18914 | 19415 | 0.97 | 160 | 14520 (0.75) | 1.30 | 160 |
| `matadd` | 35554 | 36881 | 0.96 | 161 | 28635 (0.78) | 1.24 | 161 |
| `memcpy` | 26163 | 42961 | 0.61 | 15 | 42296 (0.98) | 0.62 | 15 |
| `miss_indep` | 30355 | 36854 | 0.82 | 4 | 27493 (0.75) | 1.10 | 4 |
| `sieve` | 23886 | 37114 | 0.64 | 334 | 30209 (0.81) | 0.79 | 334 |
| `stride_walk` | 30775 | 98385 | 0.31 | 19 | 98236 (1.00) | 0.31 | 19 |

### Step by step

How many times faster each step made each kernel: cycles before
divided by cycles after, so above 1.00 is faster. The last row is the
geometric mean over the kernels.

| Kernel | Caches (`nocache` to `dm`) | 2-way data cache, predictor, prefetchers (`dm` to `pf-stride`) | Out-of-order (`pf-stride` to `ooo-32`) | Two wide (`ooo-32` to `ss`) | All together (`nocache` to `ss`) |
| --- | ---: | ---: | ---: | ---: | ---: |
| `array_sum` | 5.15 | 1.88 | 0.89 | 1.13 | 9.76 |
| `branch_alt` | 9.25 | 1.35 | 0.94 | 1.38 | 16.34 |
| `bsearch` | 7.27 | 1.17 | 1.00 | 1.25 | 10.64 |
| `calls` | 8.42 | 1.68 | 0.94 | 1.41 | 18.87 |
| `code_walk` | 4.38 | 1.52 | 1.00 | 1.00 | 6.64 |
| `conflict` | 2.80 | 4.47 | 0.98 | 1.61 | 19.89 |
| `dep_chain` | 10.48 | 1.11 | 1.00 | 1.12 | 13.11 |
| `ilp_chains` | 10.48 | 1.11 | 1.00 | 1.80 | 20.92 |
| `list_walk` | 4.14 | 1.10 | 1.28 | 1.03 | 5.99 |
| `loop_nest` | 9.35 | 1.46 | 0.99 | 1.34 | 18.14 |
| `matadd` | 6.39 | 1.87 | 1.00 | 1.29 | 15.36 |
| `memcpy` | 4.45 | 1.87 | 1.03 | 1.02 | 8.72 |
| `miss_indep` | 5.98 | 1.16 | 1.41 | 1.34 | 13.13 |
| `sieve` | 6.04 | 1.54 | 1.03 | 1.23 | 11.83 |
| `stride_walk` | 2.95 | 1.54 | 1.00 | 1.00 | 4.54 |
| Geometric mean | **5.99** | **1.54** | **1.03** | **1.24** | **11.80** |

### Data cache

Miss rate is misses per data access. Stall is the share of all cycles
the pipeline spent held waiting for the data cache.

| Kernel | Data accesses | `dm` misses | miss rate | stall | write-backs | `2way` misses | miss rate | stall | write-backs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `array_sum` | 18432 | 4608 | 25.0% | 52% | 512 | 4608 | 25.0% | 52% | 512 |
| `branch_alt` | 0 | 0 | - | 0% | 0 | 0 | - | 0% | 0 |
| `bsearch` | 2507 | 709 | 28.3% | 21% | 65 | 701 | 28.0% | 21% | 65 |
| `calls` | 800 | 1 | 0.1% | 0% | 0 | 1 | 0.1% | 0% | 0 |
| `code_walk` | 0 | 0 | - | 0% | 0 | 0 | - | 0% | 0 |
| `conflict` | 7808 | 7808 | 100.0% | 73% | 128 | 32 | 0.4% | 1% | 0 |
| `dep_chain` | 0 | 0 | - | 0% | 0 | 0 | - | 0% | 0 |
| `ilp_chains` | 0 | 0 | - | 0% | 0 | 0 | - | 0% | 0 |
| `list_walk` | 6657 | 3359 | 50.5% | 56% | 287 | 3335 | 50.1% | 56% | 263 |
| `loop_nest` | 0 | 0 | - | 0% | 0 | 0 | - | 0% | 0 |
| `matadd` | 8962 | 1729 | 19.3% | 40% | 640 | 1729 | 19.3% | 40% | 640 |
| `memcpy` | 9728 | 2432 | 25.0% | 50% | 1152 | 2432 | 25.0% | 50% | 1152 |
| `miss_indep` | 3248 | 1640 | 50.5% | 42% | 512 | 1640 | 50.5% | 42% | 512 |
| `sieve` | 5298 | 865 | 16.3% | 32% | 800 | 886 | 16.7% | 33% | 820 |
| `stride_walk` | 7680 | 7680 | 100.0% | 65% | 512 | 7680 | 100.0% | 65% | 512 |

### Instruction cache (`dm`)

| Kernel | Fetches | Misses | Miss rate | Cycles fetch waited |
| --- | ---: | ---: | ---: | ---: |
| `array_sum` | 55331 | 5 | 0.0% | 0% |
| `branch_alt` | 28769 | 5 | 0.0% | 0% |
| `bsearch` | 31768 | 8 | 0.0% | 0% |
| `calls` | 42410 | 6 | 0.0% | 0% |
| `code_walk` | 19330 | 2431 | 12.6% | 60% |
| `conflict` | 35393 | 6 | 0.0% | 0% |
| `dep_chain` | 30010 | 4 | 0.0% | 0% |
| `ilp_chains` | 30024 | 6 | 0.0% | 0% |
| `list_walk` | 31071 | 7 | 0.0% | 0% |
| `loop_nest` | 28128 | 4 | 0.0% | 0% |
| `matadd` | 40670 | 9 | 0.0% | 0% |
| `memcpy` | 36394 | 6 | 0.0% | 0% |
| `miss_indep` | 33778 | 9 | 0.0% | 0% |
| `sieve` | 37899 | 5 | 0.0% | 0% |
| `stride_walk` | 46128 | 4 | 0.0% | 0% |

### Where the cycles go on the ideal machine

| Kernel | Cycles | Taken branches and jumps | Load-use stalls | Cycles lost to control flow | Cycles lost to load-use |
| --- | ---: | ---: | ---: | ---: | ---: |
| `array_sum` | 55332 | 4607 | 0 | 17% | 0% |
| `branch_alt` | 28773 | 5000 | 0 | 35% | 0% |
| `bsearch` | 34022 | 3942 | 2251 | 23% | 7% |
| `calls` | 43217 | 10400 | 800 | 48% | 2% |
| `code_walk` | 19331 | 30 | 0 | 0% | 0% |
| `conflict` | 35398 | 3903 | 0 | 22% | 0% |
| `dep_chain` | 30011 | 1500 | 0 | 10% | 0% |
| `ilp_chains` | 30026 | 1500 | 0 | 10% | 0% |
| `list_walk` | 34146 | 3327 | 3072 | 19% | 9% |
| `loop_nest` | 28132 | 4608 | 0 | 33% | 0% |
| `matadd` | 40928 | 2558 | 256 | 12% | 1% |
| `memcpy` | 41009 | 5118 | 4608 | 25% | 11% |
| `miss_indep` | 34979 | 1711 | 1200 | 10% | 3% |
| `sieve` | 39943 | 7007 | 2041 | 35% | 5% |
| `stride_walk` | 53303 | 7679 | 7168 | 29% | 13% |

### Cache policies: configurations

All on the in-order core with the predictor of `bp2`, no prefetchers,
memory latency 10, a 1 KiB direct-mapped instruction cache and a 1 KiB
data cache of 8 sets x 4 ways. `lru` is the base; each of the others
changes what the table shows.

| Name | Replacement | A store that hits | A store that misses | Write buffer |
| --- | --- | --- | --- | --- |
| `lru` | LRU | write-back | allocate | none |
| `fifo` | FIFO | write-back | allocate | none |
| `random` | random | write-back | allocate | none |
| `plru` | pseudo-LRU | write-back | allocate | none |
| `wb-buf` | LRU | write-back | allocate | 4 entries |
| `wt` | LRU | write-through | allocate | none |
| `wt-buf` | LRU | write-through | allocate | 4 entries |
| `wt-merge` | LRU | write-through | allocate | 4 entries, merging |
| `wt-na` | LRU | write-through | no-allocate | 4 entries |
| `wt-na-merge` | LRU | write-through | no-allocate | 4 entries, merging |
| `wb-na-merge` | LRU | write-back | no-allocate | 4 entries, merging |

### Replacement policy

Data cache misses, miss rate, and cycles; cycles relative to `lru` in
brackets (below 1.00 is faster).
On `array_sum`, `branch_alt`, `calls`, `code_walk`, `conflict`, `dep_chain`, `fill`, `ilp_chains`, `loop_nest`, `memcpy`, `store_hot`, `stride_walk` all four policies
miss exactly as often, and those kernels are left out.

| Kernel | Data accesses | `lru` misses | cycles | `fifo` misses | cycles | `random` misses | cycles | `plru` misses | cycles |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `bsearch` | 2507 | 728 (29.0%) | 38342 | 798 (31.8%) | 39182 (1.02) | 764 (30.5%) | 38774 (1.01) | 636 (25.4%) | 37238 (0.97) |
| `list_walk` | 6657 | 3328 (50.0%) | 70352 | 3328 (50.0%) | 70352 (1.00) | 3345 (50.2%) | 70743 (1.01) | 3328 (50.0%) | 70352 (1.00) |
| `lru_fifo` | 7715 | 1958 (25.4%) | 47331 | 4835 (62.7%) | 81855 (1.73) | 4419 (57.3%) | 76863 (1.62) | 1958 (25.4%) | 47331 (1.00) |
| `matadd` | 8962 | 1729 (19.3%) | 63988 | 1729 (19.3%) | 63988 (1.00) | 1728 (19.3%) | 63965 (1.00) | 1729 (19.3%) | 63988 (1.00) |
| `miss_indep` | 3248 | 1644 (50.6%) | 57024 | 1642 (50.6%) | 57000 (1.00) | 1629 (50.2%) | 56844 (1.00) | 1644 (50.6%) | 57024 (1.00) |
| `sieve` | 5298 | 898 (16.9%) | 46520 | 898 (16.9%) | 46520 (1.00) | 727 (13.7%) | 42631 (0.92) | 898 (16.9%) | 46520 (1.00) |
| `ways_plus_one` | 4005 | 4005 (100.0%) | 72199 | 4005 (100.0%) | 72199 (1.00) | 1625 (40.6%) | 43639 (0.60) | 4004 (100.0%) | 72187 (1.00) |

Geometric mean of the cycles relative to `lru`, over all 19 kernels: `fifo` 1.030, `random` 0.995, `plru` 0.998.

### Write policy: cycles

`lru` is write-back with allocation and no buffer. Cycles relative to
it are in brackets (below 1.00 is faster).
Kernels that store almost nothing (`branch_alt`, `calls`, `code_walk`, `dep_chain`, `ilp_chains`, `loop_nest`, `lru_fifo`, `ways_plus_one`) are left out.

| Kernel | `lru` | `wb-buf` | `wt` | `wt-buf` | `wt-merge` | `wt-na` | `wt-na-merge` | `wb-na-merge` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `array_sum` | 107134 | 101502 (0.95) | 121972 (1.14) | 118346 (1.10) | 101512 (0.95) | 112713 (1.05) | 95850 (0.89) | 95850 (0.89) |
| `bsearch` | 38342 | 37627 (0.98) | 40187 (1.05) | 39059 (1.02) | 37700 (0.98) | 38506 (1.00) | 37014 (0.97) | 37014 (0.97) |
| `conflict` | 28156 | 28156 (1.00) | 29436 (1.05) | 29047 (1.03) | 28375 (1.01) | 29094 (1.03) | 28181 (1.00) | 28181 (1.00) |
| `fill` | 39730 | 28796 (0.72) | 69756 (1.76) | 56399 (1.42) | 33928 (0.85) | 45146 (1.14) | 16526 (0.42) | 16526 (0.42) |
| `list_walk` | 70352 | 67598 (0.96) | 72666 (1.03) | 70081 (1.00) | 70081 (1.00) | 67262 (0.96) | 64469 (0.92) | 64469 (0.92) |
| `matadd` | 63988 | 61942 (0.97) | 82548 (1.29) | 59661 (0.93) | 58268 (0.91) | 58254 (0.91) | 55806 (0.87) | 59931 (0.94) |
| `memcpy` | 72689 | 60145 (0.83) | 106097 (1.46) | 81827 (1.13) | 68817 (0.95) | 69159 (0.95) | 50271 (0.69) | 50271 (0.69) |
| `miss_indep` | 57024 | 51392 (0.90) | 71862 (1.26) | 68236 (1.20) | 51402 (0.90) | 62603 (1.10) | 45735 (0.80) | 45735 (0.80) |
| `sieve` | 46520 | 39603 (0.85) | 69993 (1.50) | 59240 (1.27) | 41675 (0.90) | 49843 (1.07) | 29896 (0.64) | 29900 (0.64) |
| `store_hot` | 33543 | 33543 (1.00) | 66813 (1.99) | 37006 (1.10) | 33615 (1.00) | 37005 (1.10) | 33604 (1.00) | 33548 (1.00) |
| `stride_walk` | 135789 | 133206 (0.98) | 135277 (1.00) | 133734 (0.98) | 133734 (0.98) | 127570 (0.94) | 127570 (0.94) | 127570 (0.94) |
| Geometric mean, all 19 kernels | 1.00 | **0.95** | **1.16** | **1.06** | **0.97** | **1.01** | **0.88** | **0.89** |

### Write policy: traffic to and from memory

For each configuration: writes sent to memory / lines read from memory
by the data cache. A write is a whole line when a dirty line is
evicted, and the bytes of one store (or of several merged ones)
otherwise.

| Kernel | `lru` | `wb-buf` | `wt` | `wt-buf` | `wt-merge` | `wt-na` | `wt-na-merge` | `wb-na-merge` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `array_sum` | 512 / 4608 | 512 / 4608 | 2048 / 4608 | 2048 / 4608 | 515 / 4608 | 2048 / 4096 | 514 / 4096 | 514 / 4096 |
| `bsearch` | 65 / 728 | 65 / 728 | 256 / 728 | 256 / 728 | 130 / 728 | 256 / 676 | 118 / 676 | 118 / 676 |
| `conflict` | 0 / 32 | 0 / 32 | 128 / 32 | 128 / 32 | 65 / 32 | 128 / 32 | 44 / 32 | 44 / 32 |
| `fill` | 994 / 1026 | 994 / 1026 | 4096 / 1026 | 4096 / 1026 | 2051 / 1026 | 4096 / 3 | 1492 / 3 | 1492 / 3 |
| `list_walk` | 256 / 3328 | 256 / 3328 | 513 / 3328 | 513 / 3328 | 512 / 3328 | 513 / 3072 | 259 / 3072 | 259 / 3072 |
| `matadd` | 640 / 1729 | 640 / 1729 | 2560 / 1729 | 2560 / 1729 | 2433 / 1729 | 2560 / 1601 | 2337 / 1601 | 801 / 1601 |
| `memcpy` | 1152 / 2432 | 1152 / 2432 | 4608 / 2432 | 4608 / 2432 | 3329 / 2432 | 4608 / 1280 | 2749 / 1280 | 2749 / 1280 |
| `miss_indep` | 512 / 1644 | 512 / 1644 | 2048 / 1644 | 2048 / 1644 | 515 / 1644 | 2048 / 1132 | 514 / 1132 | 514 / 1132 |
| `sieve` | 827 / 898 | 827 / 898 | 3257 / 898 | 3257 / 898 | 1629 / 898 | 3257 / 63 | 1441 / 63 | 1431 / 63 |
| `store_hot` | 0 / 10 | 0 / 10 | 3328 / 10 | 3328 / 10 | 3013 / 10 | 3328 / 10 | 3010 / 10 | 210 / 10 |
| `stride_walk` | 512 / 7680 | 512 / 7680 | 512 / 7680 | 512 / 7680 | 512 / 7680 | 512 / 7168 | 512 / 7168 | 512 / 7168 |

### Write buffer

Share of all cycles in which a store waited because the write buffer
was full.

| Kernel | `wb-buf` | `wt-buf` | `wt-merge` | `wt-na` | `wt-na-merge` | `wb-na-merge` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `array_sum` | 0.0% | 11.6% | 0.0% | 15.4% | 0.5% | 0.5% |
| `bsearch` | 0.0% | 2.8% | 0.0% | 3.9% | 0.0% | 0.0% |
| `conflict` | 0.0% | 2.4% | 0.0% | 3.2% | 0.0% | 0.0% |
| `fill` | 0.0% | 38.0% | 0.0% | 63.4% | 0.0% | 0.0% |
| `list_walk` | 0.0% | 0.0% | 0.0% | 4.1% | 0.0% | 0.0% |
| `matadd` | 0.0% | 2.9% | 0.0% | 4.2% | 0.0% | 0.0% |
| `memcpy` | 0.0% | 3.9% | 0.0% | 24.3% | 0.0% | 0.0% |
| `miss_indep` | 0.0% | 20.2% | 0.0% | 27.7% | 1.1% | 1.1% |
| `sieve` | 0.0% | 28.4% | 0.0% | 45.0% | 8.3% | 8.3% |
| `store_hot` | 0.0% | 9.3% | 0.0% | 9.2% | 0.0% | 0.0% |
| `stride_walk` | 0.0% | 0.0% | 0.0% | 2.8% | 2.8% | 2.8% |

### Victim buffer

`dm-vb` is `dm` with a 4-entry victim buffer beside the data cache;
`2way` is the same cache size in two ways instead. Misses are data
accesses that did not find their line in the cache; with the buffer,
the number in brackets is how many of them the buffer served, each in
one cycle and without going to memory. Cycles relative to
`dm` are in brackets (below 1.00 is faster). Kernels in which the
buffer served fewer than ten misses are left out.

| Kernel | Data accesses | `dm` misses | cycles | `dm-vb` misses (from the buffer) | cycles | `2way` misses | cycles |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `bsearch` | 2507 | 709 | 43318 | 709 (122) | 41965 (0.97) | 701 | 43222 (1.00) |
| `conflict` | 7808 | 7808 | 130550 | 7808 (6816) | 54342 (0.42) | 32 | 35830 (0.27) |
| `list_walk` | 6657 | 3359 | 77683 | 3359 (31) | 77001 (0.99) | 3335 | 77131 (0.99) |
| `miss_indep` | 3248 | 1640 | 60388 | 1640 (11) | 60267 (1.00) | 1640 | 60388 (1.00) |

Geometric mean of the cycles relative to `dm`, over all 15 kernels: `dm-vb` 0.941, `2way` 0.917.

### The hierarchy: configurations

All on the in-order core with the predictor of `bp2`, a 1 KiB
direct-mapped instruction cache and a 1 KiB data cache of 8 sets x 4
ways (the caches of `lru`), and a memory that takes 50 cycles per line
instead of 10. The second-level cache is shared by both first-level
caches, uses LRU and write-back, and answers a hit 4 cycles after it is
asked.

| Name | Second-level cache | Inclusion | Victim buffer (data) | Prefetch (instructions / data) |
| --- | --- | --- | --- | --- |
| `h-l1`: Slow memory, first-level caches only | none | - | none | none / none |
| `h-l2`: 8 KiB second-level cache | 8 KiB, 64 sets x 4 ways | non-inclusive | none | none / none |
| `h-incl`: Inclusive second-level cache | 8 KiB, 64 sets x 4 ways | inclusive | none | none / none |
| `h-vb`: Second-level cache, 4-entry victim buffer for data | 8 KiB, 64 sets x 4 ways | non-inclusive | 4 entries | none / none |
| `h-pf`: Second-level cache, prefetchers | 8 KiB, 64 sets x 4 ways | non-inclusive | none | next line / stride |

### The hierarchy: cycles

Cycles relative to `h-l1` are in brackets (below 1.00 is faster). The
last row is the geometric mean over the kernels.

| Kernel | `h-l1` | `h-l2` | `h-incl` | `h-vb` | `h-pf` |
| --- | ---: | ---: | ---: | ---: | ---: |
| `array_sum` | 312134 | 342372 (1.10) | 353246 (1.13) | 342372 (1.10) | 297778 (0.95) |
| `branch_alt` | 21489 | 21513 (1.00) | 21513 (1.00) | 21513 (1.00) | 21442 (1.00) |
| `bsearch` | 70302 | 37295 (0.53) | 37295 (0.53) | 36530 (0.52) | 36037 (0.51) |
| `calls` | 25988 | 25978 (1.00) | 25978 (1.00) | 25978 (1.00) | 25894 (1.00) |
| `code_walk` | 145735 | 38127 (0.26) | 38127 (0.26) | 38127 (0.26) | 23755 (0.16) |
| `conflict` | 29596 | 29843 (1.01) | 29843 (1.01) | 29843 (1.01) | 30203 (1.02) |
| `dep_chain` | 27225 | 27249 (1.00) | 27249 (1.00) | 27249 (1.00) | 27177 (1.00) |
| `fill` | 120690 | 120623 (1.00) | 122875 (1.02) | 120603 (1.00) | 105344 (0.87) |
| `ilp_chains` | 27292 | 27322 (1.00) | 27322 (1.00) | 27322 (1.00) | 27294 (1.00) |
| `incl_victim` | 123813 | 31905 (0.26) | 195236 (1.58) | 21616 (0.17) | 209228 (1.69) |
| `list_walk` | 213952 | 63307 (0.30) | 73515 (0.34) | 63307 (0.30) | 63320 (0.30) |
| `loop_nest` | 19394 | 19412 (1.00) | 19412 (1.00) | 19412 (1.00) | 19347 (1.00) |
| `lru_fifo` | 127251 | 38714 (0.30) | 88093 (0.69) | 38714 (0.30) | 169156 (1.33) |
| `matadd` | 158948 | 60152 (0.38) | 60152 (0.38) | 60152 (0.38) | 42772 (0.27) |
| `memcpy` | 216129 | 65136 (0.30) | 68152 (0.32) | 65136 (0.30) | 46009 (0.21) |
| `miss_indep` | 143584 | 128087 (0.89) | 131373 (0.91) | 128042 (0.89) | 123250 (0.86) |
| `sieve` | 115680 | 39680 (0.34) | 39680 (0.34) | 39680 (0.34) | 33026 (0.29) |
| `store_hot` | 34223 | 34325 (1.00) | 34325 (1.00) | 34325 (1.00) | 33899 (0.99) |
| `stride_walk` | 463509 | 512258 (1.11) | 528506 (1.14) | 512258 (1.11) | 476936 (1.03) |
| `ways_plus_one` | 232719 | 48588 (0.21) | 48588 (0.21) | 28542 (0.12) | 53578 (0.23) |
| `ws_walk` | 263814 | 70393 (0.27) | 70393 (0.27) | 70393 (0.27) | 46430 (0.18) |
| Geometric mean, all 21 kernels | 1.00 | **0.57** | **0.66** | **0.55** | **0.61** |

### The hierarchy: misses at each level

Lines asked for are the lines the two first-level caches requested
from the second level: their misses. Missed there is how many of
those the second level did not have either, and the rate is local:
per line it was asked for. Lines taken back are those an inclusive
second level removed from a first-level cache in order to evict them
itself.

| Kernel | `h-l2` lines asked for | missed there | `h-incl` lines asked for | missed there | lines taken back |
| --- | ---: | ---: | ---: | ---: | ---: |
| `array_sum` | 4613 | 4613 (100.0%) | 4649 | 4649 (100.0%) | 38 |
| `branch_alt` | 5 | 4 (80.0%) | 5 | 4 (80.0%) | 0 |
| `bsearch` | 736 | 72 (9.8%) | 736 | 72 (9.8%) | 0 |
| `calls` | 7 | 6 (85.7%) | 7 | 6 (85.7%) | 0 |
| `code_walk` | 2432 | 82 (3.4%) | 2432 | 82 (3.4%) | 0 |
| `conflict` | 38 | 37 (97.4%) | 38 | 37 (97.4%) | 0 |
| `dep_chain` | 4 | 4 (100.0%) | 4 | 4 (100.0%) | 0 |
| `fill` | 1031 | 1031 (100.0%) | 1041 | 1041 (100.0%) | 10 |
| `ilp_chains` | 6 | 6 (100.0%) | 6 | 6 (100.0%) | 0 |
| `incl_victim` | 2011 | 10 (0.5%) | 2511 | 2508 (99.9%) | 504 |
| `list_walk` | 3335 | 269 (8.1%) | 3373 | 445 (13.2%) | 52 |
| `loop_nest` | 4 | 3 (75.0%) | 4 | 3 (75.0%) | 0 |
| `lru_fifo` | 1964 | 53 (2.7%) | 2144 | 944 (44.0%) | 181 |
| `matadd` | 1738 | 201 (11.6%) | 1738 | 201 (11.6%) | 0 |
| `memcpy` | 2438 | 265 (10.9%) | 2456 | 315 (12.8%) | 21 |
| `miss_indep` | 1653 | 1119 (67.7%) | 1670 | 1146 (68.6%) | 19 |
| `sieve` | 903 | 68 (7.5%) | 903 | 68 (7.5%) | 0 |
| `store_hot` | 18 | 18 (100.0%) | 18 | 18 (100.0%) | 0 |
| `stride_walk` | 7684 | 7683 (100.0%) | 7703 | 7702 (100.0%) | 20 |
| `ways_plus_one` | 4009 | 9 (0.2%) | 4009 | 9 (0.2%) | 0 |
| `ws_walk` | 4228 | 132 (3.1%) | 4228 | 132 (3.1%) | 0 |

### Time per data access: measured and computed

On `h-l2`. Measured: 1 + the cycles the core waited for the data
cache, divided by its accesses. Formula: the access time of lecture
21, `T1 = t1 + m1 x (t2 + m2 x tmem)`, with t1 = 1 cycle, t2 = 6
(what a first-level miss waits when the second level hits: its 4
cycles and 2 in the first-level cache) and tmem = 52 more when it
misses (memory's 50 and 2 in the second-level cache). m1 is the data
cache's miss rate. m2 is the second level's, counted as the lecture
defines it, over the lines it was asked for by the level above. The
last column adds what the formula leaves out and the counters know:
every dirty line written back, at either level, to a level that takes
5 or 51 cycles to accept it. Kernels with fewer than 100 data-cache
misses are left out.

| Kernel | Data accesses | m1 | m2 | Measured | Formula | Formula with write-backs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `array_sum` | 18432 | 25.0% | 100.0% | 17.05 | 15.50 | 17.06 |
| `bsearch` | 2507 | 29.0% | 9.8% | 4.24 | 4.22 | 4.35 |
| `fill` | 4100 | 25.0% | 100.0% | 26.33 | 15.51 | 26.34 |
| `incl_victim` | 6006 | 33.4% | 0.5% | 3.10 | 3.09 | 3.10 |
| `list_walk` | 6657 | 50.0% | 8.1% | 6.29 | 6.10 | 6.35 |
| `lru_fifo` | 7715 | 25.4% | 2.7% | 2.95 | 2.88 | 2.98 |
| `matadd` | 8962 | 19.3% | 11.6% | 3.65 | 3.32 | 3.67 |
| `memcpy` | 9728 | 25.0% | 10.9% | 4.51 | 3.91 | 4.53 |
| `miss_indep` | 3248 | 50.6% | 67.7% | 30.53 | 21.85 | 30.60 |
| `sieve` | 5298 | 16.9% | 7.5% | 3.43 | 2.68 | 3.46 |
| `stride_walk` | 7680 | 100.0% | 100.0% | 62.75 | 58.99 | 62.73 |
| `ways_plus_one` | 4005 | 100.0% | 0.2% | 7.08 | 7.12 | 7.12 |
| `ws_walk` | 4224 | 100.0% | 3.1% | 8.75 | 8.62 | 8.77 |

### Working-set size: the staircase

`ws_walk` reads one doubleword per line over a working set of the
given size, round and round. Each size is run twice, with 4096 and with
8192 loads; the figures are the difference between the two runs, that is,
what the last 4096 loads cost with the caches warm. Time per load is 1 + the
cycles waited for the data cache per load. The data cache holds 1 KiB
and the second-level cache 8 KiB, which it shares with the program's
own code. Computed is the formula of the table above. The last two
columns are the same machine with an inclusive second level.

| Working set | `h-l1` miss rate | time per load | `h-l2` first-level miss rate | second-level miss rate | time per load | computed | `h-incl` time per load | lines taken back |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 256 B | 0.0% | 1.00 | 0.0% | - | 1.00 | 1.00 | 1.00 | 0 |
| 512 B | 0.0% | 1.00 | 0.0% | - | 1.00 | 1.00 | 1.00 | 0 |
| 1 KiB | 0.0% | 1.00 | 0.0% | - | 1.00 | 1.00 | 1.00 | 0 |
| 2 KiB | 100.0% | 53.00 | 100.0% | 0.0% | 7.00 | 7.00 | 7.00 | 0 |
| 4 KiB | 100.0% | 53.00 | 100.0% | 0.0% | 7.00 | 7.00 | 7.00 | 0 |
| 8 KiB | 100.0% | 53.00 | 100.0% | 0.0% | 7.00 | 7.00 | 8.69 | 32 |
| 16 KiB | 100.0% | 53.00 | 100.0% | 100.0% | 59.00 | 59.00 | 61.00 | 32 |
| 32 KiB | 100.0% | 53.00 | 100.0% | 100.0% | 59.00 | 59.00 | 61.00 | 32 |

<!-- END GENERATED -->
