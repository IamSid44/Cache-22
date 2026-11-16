# Cache-22

A 64-bit RISC-V (RV64I) processor written in SystemVerilog and built up in
steps: from a small core that ran eight instructions to an out-of-order
core that executes two instructions per cycle behind two levels of cache.

The name is a play on *Catch-22*. Several of the cache results here work
against the obvious fix: adding ways to a cache can add misses (Phase 10),
and an inclusive second level can evict the very line the program uses
most (Phase 9).

![The whole machine](docs/img/system.svg)

The processor runs in simulation (Icarus Verilog). It was not synthesised,
so every result is a count of clock cycles and nothing here says anything
about clock frequency or area.

This README goes through the processor in the order it was built. Each
phase starts from the problem with the machine as it stood, gives the idea
that fixes it, the cases that need care, how the result was checked, what
was measured, and what it leaves open. The documents in
[`docs/`](docs/) have the full detail for each phase.

## Terms used below

- **Cycle:** one tick of the processor's clock. **IPC** (instructions per
  cycle) is how many instructions finish per cycle on average.
- **Kernel:** one of 21 small benchmark programs in
  [`sw/bench/`](sw/bench/), each written so that one component changes its
  cycle count visibly.
- **Golden model:** a simple simulator of the instruction set, used as the
  reference for what every program must compute.
- **Commit trace:** one line per finished instruction (its address, the
  register it wrote, the memory it wrote). The processor and the golden
  model must produce the same trace.

## The build, phase by phase

| Phase | Adds | Measured result |
| ---: | --- | --- |
| 0 | Golden model, assembler, regression | Every later phase is checked against it |
| 1 | Five-stage pipeline for all of RV64I | 0.5 to 0.9 IPC on an instant memory |
| 2 | Exceptions | No change in speed; every fault is handled precisely |
| 3 | A 10-cycle memory, then caches | 10x slower, then 6.0x faster with caches |
| 4 | Branch prediction | Loop mispredictions fall from 4,607 to 160 |
| 5 | Prefetching | Up to 1.7x; a loss where the pattern is not learnable |
| 6 | Out-of-order execution | 1.03x on average, up to 1.4x |
| 7 | Two instructions per cycle | 1.24x on average, up to 1.8x |
| 8 | Cache policies as parameters | No policy is best on every kernel |
| 9 | A second cache level | 0.57 of the cycles with a 50-cycle memory |
| 10 | Sweeps of every cache parameter | 903 runs; a cache model matches the hardware in each |

### Phase 0: a reference to check against

- **Problem.** The starting point, a
  [course project](https://github.com/IamSid44/RISC-V-Processor) with two small cores,
  was tested by printing registers and reading them.
  That does not scale, and it finds only the mistakes one already expects.
- **Idea.** A golden model in Python executes one instruction at a time and
  writes a commit trace. Every processor in this repository must produce
  the same trace, line for line, and leave memory with the same contents.
- **Needs care.** Random test programs are generated so that instructions
  depend on each other closely, since that is what a pipeline gets wrong. A
  mutation check plants bugs in the processor one at a time to confirm
  that the tests notice each of them.
- **Details.** [VERIFICATION.md](docs/VERIFICATION.md).

![How every run is checked](docs/img/verification.svg)

### Phase 1: a five-stage pipeline

- **Problem.** A single-cycle core does all the work of an instruction in
  one clock period, so the period must fit the slowest instruction.
- **Idea.** Split the work into five stages (fetch, decode, execute,
  memory, write back) with five instructions in progress at once.
- **Needs care.** An instruction may need a result that has not been
  written yet. Results are forwarded from later stages where they exist,
  and an instruction right behind a load waits one cycle. A taken branch is
  known only in the third stage, so the two instructions fetched after it
  are discarded.
- **Checked by.** A directed program with one check per forwarding, stall
  and discard path, and 1,000 random programs against the golden model.
- **Result.** Cycles = instructions + 2 per taken branch or jump + 1 per
  load-use wait. For the `array_sum` kernel that predicts 55,330 cycles
  and the simulation takes 55,332.
- **Leaves open.** No error handling, an unrealistically fast memory, and
  two cycles lost on every taken branch.
- **Details.** [ARCHITECTURE.md, sections 2 and 3](docs/ARCHITECTURE.md#2-the-in-order-pipeline).

![The in-order core](docs/img/core_inorder.svg)

### Phase 2: exceptions

- **Problem.** An invalid instruction or a bad memory address has to stop
  the program at a well-defined point and start a handler.
- **Idea.** The fault travels down the pipeline with its instruction and is
  acted on at one fixed stage. Everything older has finished there and
  nothing younger has changed a register or memory. Such an exception is
  called precise.
- **Needs care.** The fixed stage has to be the memory stage, not the last
  one. The instruction behind the faulting one may be a store, and stores
  write memory one stage before the end.
- **Checked by.** A program with 18 checks of what must not happen around a
  fault, and random programs with injected faults (47,035 traps).
- **Result.** A trap costs three cycles. No kernel traps, so no cycle count
  changed.
- **Details.** [ARCHITECTURE.md, section 4](docs/ARCHITECTURE.md#4-precise-exceptions).

### Phase 3: a slow memory, and caches

- **Problem.** Real memory does not answer in the same cycle. With a
  10-cycle memory every kernel is about ten times slower, because every
  instruction has to be fetched from it.
- **Idea.** A cache is a small fast memory that keeps recently used data in
  lines of 32 bytes. Programs reuse what they used recently and use what
  lies next to it, so most accesses can be answered from the cache.
- **Needs care.** What the pipeline does during a miss; a fetch that is no
  longer wanted after a branch; and stores, which mark a line as changed so
  that it is written to memory before it is replaced.
- **Checked by.** Every program under eight memory arrangements, including
  a one-line cache and random memory delays, with identical traces. The
  cache alone is also tested against a plain byte array.
- **Result.** 6.0x faster on average. How much the data cache helps depends
  on the access pattern, from a 16% miss rate to 100%.
- **Leaves open.** The `conflict` kernel misses on every access because
  its two arrays map to the same cache lines. Letting each set hold two
  lines (two ways) removes that and makes it 3.6x faster. Regular miss
  patterns remain, and are the subject of Phase 5.
- **Details.** [ARCHITECTURE.md, section 6](docs/ARCHITECTURE.md#6-memory-system),
  [RESULTS.md, Phase 3](docs/RESULTS.md#phase-3-memory-latency-and-caches).

![The data cache, direct-mapped and with two ways](docs/img/caches.svg)

### Phase 4: branch prediction

- **Problem.** Fetch must choose the next address before it knows what the
  current instruction is. Always choosing the next instruction costs two
  cycles on every taken branch and jump.
- **Idea.** A table indexed by the instruction's address remembers where
  each branch went and, in a 2-bit counter, whether it is usually taken.
- **Needs care.** The table can be wrong about anything, including
  claiming that an ordinary instruction is a branch. The execute stage
  therefore compares the real next address with the predicted one for
  every instruction.
- **Checked by.** Arrangements that feed the pipeline invented predictions,
  which must not change any trace. The predictor's own decisions cannot be
  seen in a trace, so they have a separate testbench.
- **Result.** Wrong guesses in the `loop_nest` kernel fall from 4,607 to
  160, the loop exits. A 1-bit counter has 314. A branch that alternates
  stays 50% wrong.
- **Leaves open.** Alternating branches and returns from a function with
  two callers, which need a history-based predictor and a return-address
  stack. Both are out of scope.
- **Details.** [ARCHITECTURE.md, section 8](docs/ARCHITECTURE.md#8-branch-prediction),
  [RESULTS.md, Phase 4](docs/RESULTS.md#phase-4-branch-prediction).

![Branch prediction](docs/img/branch.svg)

### Phase 5: prefetching

- **Problem.** A program that walks through an array misses once per line,
  in a pattern that is entirely predictable.
- **Idea.** Fetch lines before they are asked for: the next line after
  each one used, or, per load instruction, the address a learned step
  (stride) ahead.
- **Needs care.** A prefetch must never change a result. A line can be
  asked for while it is still on its way. Only one line can be on its way
  per cache, so a useless prefetch delays a real miss.
- **Checked by.** Arrangements with a prefetcher that asks for random
  lines, and a testbench for what the prefetchers choose.
- **Result.** `array_sum` goes from 0.43 to 0.74 IPC. A next-line
  prefetcher can be 99.8% accurate and still never on time, and it makes
  the `stride_walk` kernel 46% slower, where the stride prefetcher gains.
- **Leaves open.** The pipeline still stops completely during a miss, even
  when later instructions do not depend on it.
- **Details.** [ARCHITECTURE.md, section 9](docs/ARCHITECTURE.md#9-prefetching),
  [RESULTS.md, Phase 5](docs/RESULTS.md#phase-5-prefetching).

![Prefetching](docs/img/prefetch.svg)

### Phase 6: out-of-order execution

- **Problem.** In the pipeline, one instruction that waits holds up
  everything behind it.
- **Idea.** Instructions enter and leave in program order, and in between
  each one executes as soon as its inputs are ready (Tomasulo's algorithm
  with a reorder buffer). The reorder buffer is a queue that holds each
  result until all older instructions have finished.
- **Needs care.** Loads and stores: a load may only read once the address
  of every older store is known. Exceptions and wrong branch guesses are
  acted on only when the instruction reaches the front of the reorder
  buffer.
- **Checked by.** The same traces as the pipeline, so every existing test
  applied unchanged, plus a two-entry reorder buffer arrangement and 10,000
  more random programs.
- **Result.** 1.03x on average. This core still handles one instruction per
  cycle at its entry and exit, so it removes waiting and not work. Where a
  miss can be worked around, it needs 26% fewer cycles (`miss_indep`).
- **Leaves open.** It cannot exceed one instruction per cycle.
- **Details.** [ARCHITECTURE.md, section 10](docs/ARCHITECTURE.md#10-the-out-of-order-core),
  [RESULTS.md, Phase 6](docs/RESULTS.md#phase-6-out-of-order-execution).

![The out-of-order core](docs/img/core_ooo.svg)

### Phase 7: two instructions per cycle

- **Problem.** A core that takes in and finishes one instruction per cycle
  cannot average more than one.
- **Idea.** Every stage handles up to two instructions per cycle. It is the
  same core with one parameter changed.
- **Needs care.** What the second instruction of a pair may do in the same
  cycle as the first: it may read the register the first writes, both may
  write the same register, and only simple instructions may finish second.
- **Checked by.** A generated program of 35 interacting pairs, each placed
  three ways; four properties checked in every cycle; and the one-wide
  core taking exactly its previous cycle counts.
- **Result.** 1.24x on average. Independent work reaches 1.79 IPC
  (`ilp_chains`); a chain of dependent operations stays at 1.12, which is
  the limit of that program.
- **Details.** [ARCHITECTURE.md, section 11](docs/ARCHITECTURE.md#11-two-instructions-per-cycle),
  [RESULTS.md, Phase 7](docs/RESULTS.md#phase-7-two-instructions-per-cycle).

![Three cores on the same machine](docs/img/cores.svg)

### Where the machine stands after Phase 7

![What each step bought](docs/img/speedup.svg)

Averages over 15 kernels (geometric means), against the same pipeline with
a 10-cycle memory and no caches. The memory system is worth more than
everything done to the core, which is why the remaining phases study the
cache.

### Phase 8: the design choices inside a cache

- **Problem.** The cache of Phase 3 made its choices without comparing
  them.
- **Idea.** Make each one a parameter: which line to replace (least
  recently used, first in, random, or an approximation of the first), what
  a store does on a hit and on a miss, a queue for writes on their way to
  memory, and a small buffer for lines just replaced.
- **Needs care.** A cache that replaces the wrong line still returns the
  right data, so the commit trace cannot check a policy. Each cache
  therefore writes a log of its decisions, and a second model replays the
  log and must agree with every one.
- **Result.** Most kernels miss equally often under every replacement
  policy. On `lru_fifo`, first-in-first-out needs 1.73 times the cycles of
  least-recently-used; on `ways_plus_one`, least-recently-used misses on
  every access and random replacement on 40%.
- **Details.** [ARCHITECTURE.md, section 6](docs/ARCHITECTURE.md#the-cache),
  [RESULTS.md, Phase 8](docs/RESULTS.md#phase-8-cache-policies).

![Replacement policy](docs/img/policy_repl.svg)

### Phase 9: a second cache level

- **Problem.** A first-level cache has to be small to answer in one cycle.
  With memory 50 cycles away, each of its misses is expensive.
- **Idea.** A larger, slower cache between the first level and memory,
  shared by the instruction and data caches. It can be inclusive (it holds
  every line the first level holds) or not.
- **Needs care.** Two caches share one, so an arbiter takes turns. An
  inclusive cache has to take a line away from the first level before
  replacing it.
- **Checked by.** A testbench for the three caches together, and two
  properties checked in every cycle: inclusion, and that neither
  first-level cache is kept waiting.
- **Result.** 0.57 of the cycles on average. A load costs 1, 7 or 59 cycles
  depending on which level holds its data. Inclusion costs 15% at this
  size, mostly by removing lines the first level is still using.
- **Details.** [ARCHITECTURE.md, section 6](docs/ARCHITECTURE.md#the-second-level),
  [RESULTS.md, Phase 9](docs/RESULTS.md#phase-9-a-second-cache-level).

![The staircase](docs/img/staircase.svg)

### Phase 10: sweeping the cache's parameters

- **Problem.** Phases 8 and 9 compared a few machines at one chosen size.
  Some findings might be properties of that size.
- **Idea.** Move each parameter across its range. The cache model gives
  miss counts in milliseconds, including for the best possible replacement
  policy, which needs to know the future. The processor gives cycle counts
  on a grid of 43 machines.
- **Checked by.** In each of the 903 processor runs, the model must arrive
  at exactly the hardware's counts of misses, replacements and writes.
- **Result.** Larger caches miss less, as expected, but more ways can mean
  more misses, a cache as large as the data can still miss on every
  access, and random replacement beats least-recently-used on these
  kernels. The cost of inclusion disappears once the second level is
  16 KiB.
- **Details.** [SWEEPS.md](docs/SWEEPS.md),
  [RESULTS.md, Phase 10](docs/RESULTS.md#phase-10-the-caches-parameters-swept).

![Three results that go against the rule of thumb](docs/img/anomalies.svg)

## Gains and limits

Each component on the kernel written to show what it is for:

![Where each component helped most](docs/img/gains.svg)

The same components on kernels that do not reward them:

![Where a component did not help](docs/img/no_help.svg)

## Quick start

Requirements: Python 3.10+ and [Icarus Verilog](https://bleyer.org/icarus/)
on the PATH.

```text
python tools/regress.py                     # the whole regression, about 25 minutes
python tools/regress.py rtl --core ss       # one core (inorder, ooo, ss)
python tools/regress.py rtl -c dm -k sort   # one configuration, one program
python tools/regress.py bench               # cycle counts for the kernels
python tools/regress.py sweep               # sweeps of the cache's parameters
python tools/mutation.py                    # plant bugs and check the tests catch them
python tools/rvsim.py sw/tests/fib.S        # run one program on the golden model
```

## Layout

| Folder | Contents |
| --- | --- |
| [`rtl/`](rtl/) | The processor: both cores and everything they share |
| [`tb/`](tb/) | Testbenches and the memory model |
| [`tools/`](tools/) | Assembler, golden model, generators, regression, charts, and their unit tests |
| [`sw/`](sw/) | Directed test programs and benchmark kernels |
| [`docs/`](docs/) | Architecture, verification, results, sweeps, development history |

## Documentation

- [Architecture](docs/ARCHITECTURE.md): how each block works, with
  diagrams from the whole machine down to one cache access.
- [Verification](docs/VERIFICATION.md): the golden model, the tests, and
  how every run is checked.
- [Results](docs/RESULTS.md): the measurements for each phase.
- [Sweeps](docs/SWEEPS.md): each cache parameter across its range.
- [Development history](docs/HISTORY.md): what each phase built and found,
  and the phases that were planned and not built.

## Limits

- Simulation only: no synthesis, timing or area.
- The benchmarks are small hand-written kernels. The numbers show trends
  and are not comparable with published figures.
- The golden model and assembler were written for this project and have
  not been run against Spike or `riscv-tests`.
- Interrupts, virtual memory and the multiply/divide extension are out of
  scope.

## References

The design follows Onur Mutlu's *Digital Design and Computer Architecture*
lectures (ETH Zurich, Spring 2025). Further references: Patterson and
Hennessy, *Computer Organization and Design, RISC-V Edition*; Harris and
Harris, *Digital Design and Computer Architecture, RISC-V Edition*.

## Licence

[MIT](LICENSE).
