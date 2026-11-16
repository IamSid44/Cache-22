# Development history

How the course-project processor was extended, phase by phase, into an
out-of-order RV64I core that is two instructions wide: where it started, the decisions taken,
and for each of the ten phases what was built, how it was verified, what it
found, and the criteria it had to meet before the next one began.

The original goal was reached with Phase 7. A second stage, phases 8 to 10,
builds on it: a cache hierarchy whose every design choice is a parameter,
and sweeps over those parameters. The project ends there. Three more phases
were planned (an out-of-order core that gains more from being out of order,
then several cores sharing memory) and were not built; section 6 keeps their
plans, as future work.

The companion document [VERIFICATION.md](VERIFICATION.md) defines the
platform, the golden model and the checking method that every phase relies on.

![How the project grew](img/growth.svg)

## 1. Starting point

The [course project](https://github.com/IamSid44/RISC-V-Processor) is a single-cycle core and
a 5-stage pipelined core with forwarding, load-use stalls and branch flush.
Both run eight instructions: `add sub and or addi ld sd beq`.

Both cores still work: during development the regression simulated each of
their test programs on both and compared the final state against a reference
model (31 of 31 passed).
Building that reference model meant pinning down exactly what the RTL does,
which turned up the following departures from standard RISC-V. None of them
is fixed in the course project; the new core is written to avoid each one.

The course project, its reference model and that regression stage stay in
the course project's repository. The group counts recorded for each phase
below include those 31 groups, which the regression in this repository
does not run.

| # | Course-project behaviour | Standard behaviour | Fixed in |
| --- | --- | --- | --- |
| 1 | Branch target is `pc + 2*imm`: `imm_gen` already appends the zero bit and `sl1` shifts again. Test programs compensate by halving offsets. | `pc + imm` | Phase 1 |
| 2 | Instruction bytes are stored big-endian. | Little-endian | Phase 1 |
| 3 | Data memory is 1024 separate 64-bit words indexed by `addr[9:0]`; address 1 and address 2 do not overlap. | Byte-addressed memory | Phase 1 |
| 4 | `funct3` is ignored outside R-type. Every OP-IMM runs as `addi` (the "fault" test's `slli x3,x3,4` adds 4), every load is 64-bit, every branch is `beq`. | Decoded per instruction | Phase 1 |
| 5 | Unknown opcodes silently become no-ops. | Illegal-instruction exception | Phase 2 |
| 6 | The register file is written on the falling clock edge to get write-before-read. | Single-edge design with an internal bypass | Phase 1 |
| 7 | No halt mechanism: the testbench stops when it fetches an all-zero word, so programs are padded with no-ops to let the pipeline drain. | Program signals completion itself | Phase 1 |
| 8 | Tests are checked by reading printed register values. | Automatic comparison against a golden model | Phase 0 |
| 9 | The program file name is a parameter inside `instruction_mem.v`; switching tests means editing RTL. | Program chosen on the command line | Phase 0 |

## 2. Goal and guiding rules

**Goal:** a 2-wide out-of-order RV64I core with a reorder buffer, precise
exceptions, branch prediction, caches and a prefetcher, where every feature
is measured and every phase is checked against one golden model.

Rules that keep it from becoming too complex:

1. **One feature per phase.** A phase ends with a green regression and a
   measured result; nothing from the next phase starts before that.
2. **Correctness is defined once.** Every core must produce the same commit
   trace as the golden model. New microarchitecture never needs a new notion
   of "correct", only new tests that stress it.
3. **Simplest design that teaches the concept.** Sizes are parameters with
   small defaults. Optimisations are added only after a counter shows the
   need.
4. **Earlier cores stay alive.** The in-order core is kept and regressed
   after the out-of-order core exists; it is the performance baseline.
5. **Simulation only.** The deliverable is functional correctness
   demonstrated in the Icarus Verilog simulator. Nothing is synthesised;
   clock frequency, area and power are not evaluated, so every performance
   claim is in cycles and instructions per cycle.
6. **Every feature is measured.** Each phase ships
   benchmark kernels chosen to show what the new component buys, and where
   it does not help (section 5).

**Goal of the second stage (phases 8 to 10):** the same core in a memory
system whose every design choice is a parameter that can be measured
(replacement policy, write policy, associativity, size, a second level,
inclusion), and then every one of them measured across its range.

Three more rules apply to it:

1. **The earlier machine does not change.** Every new option is a parameter
   whose default is today's behaviour. After each phase the existing columns
   of [RESULTS.md](RESULTS.md) must come out identical, cycle for cycle.
2. **What the commit trace cannot see gets its own reference.** A cache that
   evicts the wrong line still returns the right data, so no program can
   tell. Cache behaviour is therefore checked against a second model, event
   for event (Phase 8).
3. **The default regression stays under about half an hour.** New
   configurations take a share of the random programs instead of adding to
   them; a `--full` option runs everything.

## 3. Decisions

Fixed before the work began:

| Topic | Decision |
| --- | --- |
| Tools | Icarus Verilog and Python on Windows only. No GNU toolchain, Spike or Verilator. |
| HDL | The SystemVerilog subset that `iverilog -g2012` accepts: packages, enums, packed structs, `always_ff`/`always_comb`. Checked on the installed Icarus 12 before any RTL was written. |
| ISA | RV64I plus the machine-mode pieces needed for traps: `mstatus mtvec mepc mcause mtval mscratch`, the CSR instructions, `ecall ebreak mret`. |
| Scope | Simulation in Icarus Verilog only, verifying functional correctness. No synthesis, FPGA or timing work. |
| Branch predictor | Branch target buffer plus 2-bit saturating counters, and nothing beyond that (no gshare). |
| Benchmarks | Test programs that make the benefit of each added component visible, with a number for each. |
| Second stage | After Phase 7: a cache hierarchy with configurable replacement policies, write policies, associativities and sizes; then the out-of-order improvements; then multicore. In that order (phases 8 to 13). |
| Where it ends | After Phase 10, the sweeps. The out-of-order improvements and multicore are not built; their plans stay in section 6 as future work. |

Consequences of "Windows only" worth knowing up front: test programs are
written in assembly for the in-repo assembler (no C, no CoreMark, no official
`riscv-tests`), and the golden model is the in-repo Python simulator rather
than Spike. [VERIFICATION.md](VERIFICATION.md#7-known-limits) covers how that
risk is managed.

Design choices made at the start, with the reason for each:

| Topic | Choice | Why |
| --- | --- | --- |
| Out-of-order style | Tomasulo with a reorder buffer: the rename table points at ROB entries, results live in the ROB until commit, the architectural register file is written at commit. | It is the design DDCA lectures 13-14 develop, and needs no free list or physical register file. |
| Misprediction recovery | First version flushes when the branch reaches the ROB head; recovery at execute time is an optional follow-up. | Head-of-ROB recovery reuses the exception path, so one mechanism covers both. |
| Memory disambiguation | Stores write memory at commit. A load waits until all older store addresses are known, then forwards from the store queue or reads the cache. No dependence prediction. | Conservative scheme from lecture 15c; always correct. |
| Width | 2-wide fetch, rename, dispatch and commit. | Smallest width that forces the superscalar problems (intra-group dependencies, partial groups). |
| Caches | Separate I and D caches with parameterised sets and ways; write-back with dirty bits; LRU. (Built in Phase 3.) | Direct-mapped and set-associative are the same module with a different parameter. |
| Interrupts, virtual memory, M extension | Out of scope (section 6). | Each is a project of its own. |

Design choices for the second stage:

| Topic | Choice | Why |
| --- | --- | --- |
| Replacement policies | LRU, FIFO, random and tree pseudo-LRU. | The four that textbooks compare; each has a kernel on which it wins or loses. |
| Write policies | Write-back or write-through on a hit; allocate or not on a miss; an optional write buffer. All four combinations are legal. | Two independent choices, plus the buffer that makes write-through usable. |
| Second level | One L2 shared by the instruction and data L1 caches, built from the same cache module. One line size at every level. | A shared L2 is also what several cores would need. |
| Inclusion | Non-inclusive by default (each level evicts on its own), inclusive as an option. No exclusive hierarchy. | Inclusive adds back-invalidation, which is the interesting correctness case; exclusive adds a swap path for little new insight. |
| Machine for the hierarchy | L1 caches of 1 KiB as today, an 8 KiB L2 answering in 4 cycles, memory answering in 50. RAM stays 64 KiB. Starting values, tuned when the kernels exist. | Working sets must be able to fit L1, fit only L2, or fit neither. |
| Charts | SVG files written by a small plain-Python plotter; no new dependency. | Sweeps read far better as curves than as tables. |

## 4. Phases

Each phase lists what is built, how it is verified beyond the standing rule
(*all earlier tests still pass and the commit trace matches the golden
model*), and the exit criteria. The lecture numbers refer to Onur Mutlu's
DDCA Spring 2025 series; each lecture was read before its phase started,
and the phase notes say where the design follows it and where it does not.

| Phase | Adds | DDCA lectures |
| --- | --- | --- |
| 0 | Infrastructure: restructure, assembler, golden model, regression | 5b, 6 |
| 1 | RV64I in-order 5-stage core | 10, 11, 12 |
| 2 | Precise exceptions and machine-mode traps | 13 |
| 3 | Memory hierarchy: latency, I/D caches | 20b, 21, 22, 23a |
| 4 | Branch prediction (BTB + 2-bit counters) | 15b |
| 5 | Prefetching | 23c, 25a |
| 6 | Out-of-order execution with a reorder buffer | 13, 14, 15c |
| 7 | 2-wide superscalar | 15a |
| 8 | Cache policies (replacement, write) and a cache reference model | 22, 23a |
| 9 | A second cache level, inclusion, victim buffer | 21, 23a, 23b |
| 10 | Design-space sweeps, miss classification, charts | 22, 23a |

Phases 0 to 10 are done, and the project ends with Phase 10. Phases 11 to
13 (the out-of-order improvements, then two phases of multicore) were
planned and not built; section 6 has their plans.

### Phase 0 - Infrastructure (done)

- The course project set aside unchanged, and a reference model written
  for its two cores.
- `rvasm.py`: RV64I + Zicsr assembler with labels, data directives and the
  common pseudo-instructions.
- `rvsim.py`: golden model that writes the commit trace.
- Seven self-checking programs in `sw/tests/` covering every RV64I
  instruction, every synchronous exception the platform defines, and two
  small kernels.
- `regress.py`: runs the unit tests, the programs on the golden model, and
  the course-project RTL against its reference model.

Exit criteria met: at the end of Phase 0, `python tools/regress.py` reported
39 of 39 passing.

### Phase 1 - RV64I in-order core (done)

`rtl/inorder/inorder_core.sv`: a clean 5-stage pipeline in SystemVerilog,
fixing quirks 1-4, 6, 7 and 9 from section 1.

- Full RV64I decode; behavioural ALU (the course project's gate-level ALU is not reused).
- Byte-addressed little-endian memory; one memory model shared by fetch and
  data, loaded with `$readmemh`. The core passes an address, a size and
  right-justified data, and the memory places the bytes.
- Posedge-only design; register file with internal write-to-read bypass.
- Branches and jumps resolved in EX, predict-not-taken, two-cycle flush.
- Halt and console through the two memory-mapped registers.
- A commit-trace port at writeback (pc, instruction, register write, memory
  write) that the testbench prints in the golden model's format.
- `regress.py rtl` stage: run each program on RTL, compare traces line by
  line, report the first divergence.
- `tools/rvgen.py`: random program generator (see VERIFICATION.md section 4).
- The benchmark kernels of section 5 in `sw/bench/`, and the `bench` stage
  that produces `docs/RESULTS.md`. Phase 1 fills in the baseline column.
- `tools/mutation.py`: plants bugs in the RTL to confirm the tests see them
  (added during the phase; not in the original plan).

Not done in this phase: instructions that trap. The decoder flags illegal
instructions and SYSTEM instructions, and the testbench stops with an error
if one reaches commit, but misaligned or out-of-range accesses and
misaligned jump targets are not detected until Phase 2. No Phase 1 program
contains any of these.

Exit criteria met: all `sw/tests` except `traps.S` (7 programs), all 14
kernels in `sw/bench`, and 1,000 random programs (1.28 million instructions)
match the golden trace; `python tools/regress.py --seeds 1000` reports 76 of
76 passing with `traps.S` skipped; the mutation check catches 14 of 14
planted bugs; the baseline table is in `docs/RESULTS.md`.

### Phase 2 - Precise exceptions and traps (done)

- `rtl/common/csr_file.sv`: the machine-mode CSRs of section 3, trap entry
  and `mret`. The decoder gained the SYSTEM instructions and decides CSR
  legality from the instruction bits.
- Exception information travels down the pipeline with its instruction and
  is acted on in one fixed stage, where younger instructions are squashed
  and the pc is redirected to `mtvec`. This is the precise-exception rule of
  lecture 13 applied to a pipeline whose stages already retire in order.
- That stage is MEM, not the last stage as first planned. The instruction
  behind a trapping one may be a store, and stores write memory in MEM;
  taking the trap in WB would let that store through one cycle too early.
  In MEM, everything younger is still in a stage that has changed nothing.
  CSR instructions and `mret` execute in MEM as well, which keeps all CSR
  activity in program order. [ARCHITECTURE.md](ARCHITECTURE.md) section 4
  has the full argument and a timeline.
- All nine causes of the platform: fetch outside RAM, illegal instruction,
  `ecall`, `ebreak`, misaligned jump target, misaligned and out-of-range
  load and store.
- `rvgen.py --traps`: random programs with faulting instructions, a handler
  that can be told to skip the instruction after a fault, and CSR traffic.
  Odd seeds in the regression use it.
- `sw/tests/precise.S`: 18 checks of what must not happen around a trap.

Why no reorder buffer yet: a 5-stage pipeline retires in program order by
construction, so a reorder buffer would have nothing to reorder. It arrives
in Phase 6, where it becomes the mechanism that makes exceptions precise.
Lecture 13 was read for this phase; its reorder-buffer design (the register
file holds a tag that points at the ROB entry, so the ROB needs no
associative search) is the one section 3 already proposes for Phase 6.

Performance is unchanged: no kernel traps, and every cycle count in
`docs/RESULTS.md` is identical to Phase 1. A trap or `mret` costs 3 cycles.

Found on the way: four random programs appeared to hang the RTL. The core
was right; the golden model was reporting its `minstret` register as its
instruction count, so programs that wrote `minstret` gave the regression a
nonsensical cycle limit. The statistic and the register are now separate,
with a unit test.

Exit criteria met: `traps.S` (20 traps, every cause it can raise) and
`precise.S` match the golden trace; `precise.S` shows a store, a register
write and a CSR write behind a faulting instruction never taking effect;
1,000 random programs, 500 of them with injected faults, match (1.74 million
instructions and 47,035 traps); `python tools/regress.py --seeds 1000`
reports 79 of 79 passing; the mutation check catches 30 of 30 planted bugs.

### Phase 3 - Memory hierarchy (done)

- `tb/sim_memory.sv`: main memory transfers whole 32-byte lines and answers
  after a configurable latency, optionally with a random extra delay.
- `rtl/common/cache.sv`: one parameterised cache used for both instruction
  and data: any number of sets and ways, true LRU, write-back with dirty
  bits, write-allocate. It can be disabled, which turns it into a wire to
  memory.
- `rtl/common/mem_subsystem.sv`: the two caches plus the memory map.
- The pipeline handles a miss in two ways: fetch waits and feeds bubbles
  while later stages continue; a data miss holds every stage still.
  [ARCHITECTURE.md](ARCHITECTURE.md) section 6 explains both.
- Counters for cache accesses, misses, write-backs and stall cycles.
- With both caches disabled and zero latency the machine is, cycle for
  cycle, the Phase 2 machine. That configuration is kept as `ideal`.

Verification specific to this phase:

- The regression now runs every directed program and kernel under eight
  memory configurations (ideal, no caches, a one-line cache, direct-mapped,
  2-way, 4-way, and each cache alone), five of them with random memory
  latency, and spreads the random programs across them. The commit trace
  must be identical in all of them.
- `tb/cache_tb.sv` tests the cache alone against a byte-array model, for six
  geometries at three memory timings each, including abandoned reads and a
  direct check of the LRU order. It is the new `blocks` stage.
- Fifteen memory and cache bugs were added to the mutation check.

Lectures 21, 22 and 23a were read for this phase. They agree with the design
(split first-level caches, write-back with a dirty bit, write-allocate), and
one point was adopted from them: stall time is reported next to miss rate,
because a miss count alone does not say what a miss costs.

Two things went wrong on the way and are worth recording. The mutation
check, which now runs every planted bug under several configurations, took
several times longer than expected; stopping it left a planted bug in the
core, which was found by checking for it and then reverted. The tool now
backs up and restores the file it edits, can confirm the tree is clean, and
runs about five times faster. Separately, the check exposed a blind spot of
simulation itself (uninitialised tags hide a missing valid-bit check), fixed
in the cache testbench. VERIFICATION.md section 4 has both.

Exit criteria met: every directed program, every kernel and 1,000 random
programs produce the golden trace in all eight memory configurations
(`python tools/regress.py --seeds 1000`: 77 of 77 passing); the cache
testbench passes for six geometries at three memory timings; the mutation
check catches 44 of 44 planted bugs; miss rates, stall time and cycle counts
for four configurations are in `docs/RESULTS.md`.

### Phase 4 - Branch prediction (done)

- `rtl/common/bpred.sv`: a direct-mapped branch target buffer looked up in
  the fetch stage from the pc alone, with a 2-bit saturating counter in each
  entry. One table serves conditional branches, jumps and returns.
- The predicted next pc travels with each instruction. The execute stage
  works out the real next pc of *every* instruction and redirects fetch if
  they differ. One comparison covers a wrong direction either way, a wrong
  target, and an ordinary instruction mistaken for a branch.
- The predictor is trained in the execute stage with what really happened.
- Counters for branches, jumps and the mispredictions of each.
- A 1-bit ("last time") variant of the counter is kept as a parameter so the
  benefit of the second bit can be measured rather than asserted.
  [ARCHITECTURE.md](ARCHITECTURE.md) section 8 has the design.

Stopping at 2-bit counters was a deliberate limit of scope: no global
history, no return-address stack. `docs/RESULTS.md` shows what that leaves
on the table (alternating branches, and returns from a function with more
than one caller).

Verification specific to this phase:

- Trace comparison cannot test a predictor's quality: recovery hides every
  bad prediction. So the predictor's policy has its own testbench
  (`tb/bpred_tb.sv`): what is predicted after which history, hysteresis,
  saturation, take-over of an entry, and the lecture's numbers for a loop
  and for an alternating branch.
- Recovery is tested the opposite way, by making predictions as bad as
  possible. Three verification configurations mix garbage into the
  predictions: "taken" for instructions that are not branches, to targets
  that are nearby, random, misaligned or outside memory. The commit trace
  must still be identical.
- Other configurations use a 1-entry, a 4-entry and an aliasing (1-bit tag)
  predictor. `sw/tests/predict.S` trains the predictor one way and then does
  the other, for each kind of misprediction.
- Twelve predictor and recovery bugs were added to the mutation check.

Lecture 15b was read for this phase. The design matches it (BTB indexed by
the fetch pc, a hit means "this is a branch", the top counter bit is the
direction, training after execution). Its 2-bit counter starts at "weakly
taken"; here a new entry starts at the maximum, which is the same thing one
step later, since an entry is only created by a taken branch.

Exit criteria met: every directed program, every kernel and 1,000 random
programs produce the golden trace in all ten configurations, three of which
feed the pipeline garbage predictions (`python tools/regress.py --seeds
1000`: 86 of 86 passing); the predictor testbench passes in four
configurations; the mutation check catches 56 of 56 planted bugs;
misprediction counts and cycle counts with no predictor, a last-time
predictor and the 2-bit predictor are in `docs/RESULTS.md`.

### Phase 5 - Prefetching (done)

- `rtl/common/prefetcher.sv`: watches the accesses to a cache and proposes
  lines to fetch. Two real policies: next line, and a stride table indexed
  by the pc of the load or store, with a 2-bit confidence and a prefetch
  distance. A third mode proposes random lines, for verification.
- The cache gained a prefetch port and was reorganised around one idea: a
  line "on its way" is handled the same whether the processor is waiting
  for it or not, and hits are served throughout. Prefetched lines go
  straight into the cache; each is marked until its first use, which is what
  the statistics count. [ARCHITECTURE.md](ARCHITECTURE.md) sections 6 and 9.
- Statistics follow DDCA lecture 23c: accuracy (used / issued), coverage
  (prefetched misses / all misses) and timeliness (on time / used).
- `sw/bench/code_walk.S`, a generated kernel of straight-line code larger
  than the instruction cache, because none of the hand-written kernels
  misses in the instruction cache at all.

Simplifications that show in the results: one line can be on its way per
cache, and a demand miss that arrives while a prefetch is on its way waits
for it. An inaccurate prefetcher therefore costs time, not just cache
space, and the results show it clearly.

Verification specific to this phase:

- Five verification configurations now prefetch, three of them with random
  lines at random moments, on one cache or both. Results must not change.
- The cache testbench runs with an independent stream of random prefetch
  requests throughout, and checks directly that a prefetched line is there
  without a wait, is counted once, is not fetched twice, and that hits are
  served while another line is on its way.
- `tb/prefetcher_tb.sv` checks the policy, which no program can see: when
  proposals start, the address proposed, a negative stride, two instructions
  tracked separately, silence on an irregular pattern, recovery after the
  jump back at the end of a pass, relearning a new stride.
- Fourteen cache and prefetcher bugs were added to the mutation check.

Lectures 23c and 25a were read for this phase. The four questions they pose
have these answers here. What: next line, or address + stride x distance.
When: on every access, as soon as the cache is idle. Where: into the cache
itself, in front of main memory. How: in hardware, with no help from the
program.

Exit criteria met: every directed program, every kernel and 1,000 random
programs produce the golden trace in all eleven configurations, three of
which prefetch random lines (`python tools/regress.py --seeds 1000`: 93 of
93 passing); the cache testbench passes with random prefetch traffic and
the prefetcher testbench passes in four configurations; the mutation check
catches 70 of 70 planted bugs; misses, accuracy, coverage and timeliness
per kernel for both prefetchers are in `docs/RESULTS.md`.

### Phase 6 - Out-of-order execution (done)

- `rtl/ooo/ooo_core.sv`: Tomasulo's algorithm with a reorder buffer. A
  register alias table that points at ROB entries, results kept in the ROB
  until retirement, reservation stations, a load/store queue, one common
  data bus, and retirement in order from the ROB head.
  [ARCHITECTURE.md](ARCHITECTURE.md) section 10.
- It reuses the decoder, ALU, register file, CSR file, branch predictor and
  the whole memory subsystem, and has the same ports as the in-order core,
  so the testbench and every test apply to it unchanged.
- Exceptions and mispredictions are recorded in the ROB entry and acted on
  at the head: everything in flight is discarded and the RAT is cleared.
  This is the reorder-buffer form of precise exceptions.
- CSR instructions and `mret` are serialised at the ROB head.
- The in-order core is kept and still regressed; it is the baseline the
  out-of-order core is measured against.

Changes from the plan:

- **No store-to-load forwarding.** A load that overlaps an older store waits
  for the store to retire and then reads the cache. Forwarding would let a
  value in the queue decide what a load returns, including for addresses
  where the memory system would have reported a fault. The lecture lists
  waiting for retirement as part of the conservative option.
- **Not built in three steps.** The plan was registers first, then branches,
  then memory. The core was written whole and passed the directed programs
  on its first build, so the intermediate steps were skipped.
- **Recovery at retirement only**, as planned for the first version.
  Recovery at execution was left out.

Verification specific to this phase:

- Everything that already existed applies: all directed programs, kernels
  and random programs, in every configuration, now on both cores.
- Three more configurations for this core vary the window: a 2-entry ROB
  with one reservation station and a 2-entry queue, where every structure
  is full nearly all the time; a 4-entry ROB combined with random latency,
  garbage predictions and random prefetches; and a 32-entry ROB.
- `sw/tests/ooo.S` arranges for a younger instruction to be ready before an
  older one that it must respect: a load behind a store whose address or
  data is late, partial overlaps, a renamed register with a reader still
  waiting, wrong-path work that must leave no trace, and an older late
  fault against a younger early one.
- Twenty-six bugs planted in this core were added to the mutation check.

Lectures 14 and 15c were read for this phase, and 13 before it.

Exit criteria met: on both cores, every directed program, every kernel and
1,000 random programs produce the golden trace in every configuration
(`python tools/regress.py --seeds 1000`: 122 of 122 passing, 17 minutes);
on the out-of-order core a further 10,000 random programs, spread over its
fourteen configurations, also match (17.5 million instructions and 482,067
traps, 31 minutes); the mutation check catches 96 of 96 planted bugs; the
two cores are compared kernel by kernel on the same machine in
`docs/RESULTS.md`.

### Phase 7 - Superscalar (done)

- The out-of-order core has a `WIDTH` parameter. At 2 it fetches,
  dispatches, executes and retires up to two instructions per cycle.
  [ARCHITECTURE.md](ARCHITECTURE.md) section 11.
- **Fetch** takes the instruction at pc + 4 from the same cache access when
  it lies in the same line, and consults the predictor for both. A predicted
  taken branch ends the pair.
- **Rename** handles the pair: the second instruction waits for the first
  one's tag if it reads the register the first writes, and the rename table
  ends up pointing at the second if both write one register.
- **Execute** has two execution units and two result buses. A load that
  completes uses one bus.
- **Retire** takes the entry behind the reorder-buffer head with the head
  when that entry only has a register to write. Traps, stores and system
  instructions still happen at the head, one per cycle, on the logic
  Phase 6 verified.
- It is one RTL for both widths. The width-1 core is kept, regressed in
  full, and takes exactly the same number of cycles on every kernel as
  before the change.
- Shared blocks gained second ports: a second lookup and training port on
  the branch predictor, a second retirement input on the CSR block, and the
  second instruction of a fetch out of the memory subsystem. The cache did
  not change.

Changes from the plan:

- **"A trap or misprediction on either slot"** is handled by keeping traps
  out of the second slot: an instruction that will trap is never retired
  second, so it reaches the head one cycle later and traps there. A
  misprediction by the second instruction is handled in place.
- **One load and one store per cycle.** The data cache keeps its single
  port and the load/store queue its single address adder. Memory-bound
  kernels therefore gain nothing from width, which the results show.
- **No fetch queue.** When only the first of a pair can be dispatched the
  second moves up and fetch waits a cycle.

Verification specific to this phase:

- The regression has a third core, `ss`, which is the out-of-order core at
  width 2. Everything that already existed runs on it, in all eleven shared
  configurations and four window configurations, one of them (`ss-pair`) a
  reorder buffer with room for exactly one pair under garbage predictions.
- `sw/tests/pairs.S` (generated by `sw/tests/gen_pairs.py`): 35 sequences in
  which the two instructions of a pair interact, each placed three ways so
  that different instructions end up side by side.
- The predictor testbench checks the second lookup and training ports.
- Four more invariants are checked every cycle: the buses never carry the
  same entry, a younger instruction never moves ahead of an older one, a
  system instruction is alone in the reorder buffer, and at width 1 the
  second slot is never used.
- Forty-two bugs were added to the mutation check, most of them specific to
  the pair: a dependence inside a pair missed, the wrong one of two writers
  kept, a pair dispatched into one free entry, the second retiring when it
  must not.

Lecture 15a was read for this phase. Its definition is the one used here
(fetch, decode, execute and retire several instructions per cycle, with the
hardware checking dependences between instructions fetched together), and
so is its remark that superscalar and out-of-order execution are separate
ideas: this repository now has three of the four combinations.

Exit criteria met: on all three cores, every directed program, every kernel
and 1,000 random programs produce the golden trace in every configuration
(`python tools/regress.py --seeds 1000`: 153 of 153 passing, 32 minutes); on
the two-wide core a further 10,000 random programs, spread over its fifteen
configurations, also match (17.5 million instructions and 482,067 traps, 43
minutes); the mutation check catches 138 of 138 planted bugs (56 minutes);
eight of the fifteen kernels run above one instruction per cycle,
`ilp_chains` at 1.79; width 1 and width 2 are compared kernel by kernel on
the same machine in `docs/RESULTS.md`.

This was the last phase of the original plan.

### Phase 8 - Cache policies and a cache reference model (done)

Until this phase `rtl/common/cache.sv` had any number of sets and ways but
one behaviour: LRU replacement, write-back, write-allocate. The behaviour is
now a set of parameters, and a second reference model checks it.

Parameters of the cache (the first value of each is the earlier cache):

| Parameter | Values |
| --- | --- |
| `REPL` | 0 LRU, 1 FIFO, 2 random, 3 tree pseudo-LRU (ways a power of two) |
| `WRITE_HIT` | 0 write-back: the line is marked dirty and written when evicted. 1 write-through: every store also goes to memory, lines are never dirty, evictions never write. |
| `WRITE_MISS` | 0 allocate: the line is brought in and then written. 1 no-allocate: the store goes to memory and the line is not brought in. |
| `WBUF_ENTRIES` | 0 none. Otherwise a queue of that many writes between the cache and memory. |
| `WBUF_MERGE` | 1: a store to a line that already has a queued write joins that entry. |

- Every replacement policy uses an empty way before evicting a full one.
- The random policy takes its numbers from a shift register that steps once
  per line brought in, not once per cycle, so its choices do not depend on
  timing and the model can reproduce them.
- Without a write buffer, a store that has to reach memory waits for it.
  With one, the store is finished when its write is in the queue. Dirty
  victims go through the queue as well, so a miss no longer waits for a
  write-back.
- The rule the buffer rests on: a line is not read from memory while a
  write to it is queued or on its way. Nothing is forwarded out of the
  queue.
- Merging was not in the plan. It was added when the first measurement of
  `fill` showed four writes per line under no-allocate, which made
  no-allocate slower than allocating (rule 3 of section 2: an optimisation
  is added after a counter shows the need).
- New counters: lines read from memory, writes sent to memory, lines
  evicted, cycles a store waited for a full buffer.
- [ARCHITECTURE.md](ARCHITECTURE.md) section 6 describes all of it.

The reference model, `tools/cachesim.py`:

- It models one cache: which lines are present, in which way, which are
  dirty, what is on its way to memory, and what each policy does next. It
  has no notion of time and holds no data.
- In simulation a cache writes one line per event to a log (`+dcache_log=`,
  `+icache_log=`, and `+log=` in the cache testbench). The model replays the
  log: what the cache was asked comes from the log, what it decided is
  checked. [VERIFICATION.md](VERIFICATION.md) section 5 lists the checks.
- It also runs on its own as a cache simulator, which Phase 10 uses.

One more comparison, for every run of every core: **the final memory
image.** When a run ends the testbench lays everything the data cache still
holds over memory and prints a checksum, which must equal that of the golden
model's memory. Before, a store's value was checked when it committed, but
whether it was still there at the end was only checked if a later load
happened to read it.

Kernels (section 5): `lru_fifo`, `ways_plus_one`, `fill`, `store_hot`.

Verification specific to this phase:

- Nine new verification configurations, on all three cores: each
  replacement policy on a tiny cache with random latency; write-through with
  no buffer and with a one-entry buffer; no-allocate with and without a
  merging buffer; write-back through a one-entry buffer in front of a
  one-line cache; all with random prefetches or garbage predictions behind
  them; and no-allocate behind a next-line prefetcher.
- A generated directed program, `sw/tests/wpolicy.S` (18 checks): stores
  that are queued, merged or going round the cache while their line is
  evicted, wanted again, written again or on its way.
- `tb/cache_tb.sv` runs 22 combinations of shape and policy at three memory
  timings. It keeps its data check, compares memory itself at the end, and
  writes the event log, which the model must agree with.
- 28 unit tests for the model (`tools/tests/test_cachesim.py`): each policy on
  sequences worked out by hand, the two figures of lecture 22 for LRU, and
  hand-written logs containing each kind of mistake.
- 26 planted bugs: 14 that lose or corrupt data, each caught by a commit
  trace or the final memory image, and 12 that only change what the cache
  decides, each caught by the model and by nothing else.
- With every parameter at its default, every existing table of
  [RESULTS.md](RESULTS.md) is unchanged, row for row.
- The regression now makes three comparisons per run and has eight more
  configurations per core. To keep the default run to about half an hour, the
  kernels run in full on the five machines they are measured on and a third
  of them, a different third each time, in each other configuration;
  `--full` runs everything everywhere.

Lectures 22 and 23a were read again for this phase. They agree with the
design on every point they cover: evict an invalid block first; the same
list of write-back against write-through and allocate against no-allocate
trade-offs; LRU thrashing on a set asked for one line more than it holds,
with random replacement doing better (the lecture's example is now the
kernel `ways_plus_one`). Two things here are not from the lectures: the
tree form of pseudo-LRU (the lecture names not-MRU and hierarchical LRU as
approximations) and the write buffer, which the lectures do not cover. One
definition in the plan for Phase 10 was corrected from them: a capacity miss
is one that a fully associative cache with *optimal* replacement would also
have, not one with LRU.

Three things the phase found:

- **A flaw in the Phase 3 cache.** A hit and the arrival of a new line in
  the same set in the same cycle left the line that was hit looking older
  than it was, for three or more ways. Results were always correct, the ages
  were still a permutation, and no benchmark has more than two ways, so
  nothing had shown it. The planted bug that puts the old behaviour back is
  caught by the model alone.
- **A flaw in the first version of the new cache**, found by the model in
  its first run on a core: an unknown address from an idle core reached the
  replacement state and the cache evicted way 0 from then on. Every program
  still passed.
- **A textbook expectation that needed a qualifier.** Not allocating on a
  store miss made a plain array fill slower, not faster, until the write
  buffer merged stores (RESULTS.md, Phase 8).

Exit criteria met: on all three cores, in every policy configuration, the
commit trace and the final memory image equal the golden model's and both
caches' event logs agree with the cache model, on every directed program,
every kernel and 1,000 random programs (`python tools/regress.py --full
--seeds 1000`: 229 of 229 groups, 59 minutes); the cache testbench passes in
all 22 combinations at three timings; the mutation check catches 164 of 164
planted bugs; RESULTS.md
has a table per policy with misses, traffic to memory and cycles, and each
of the four new kernels shows the effect it was built for.

### Phase 9 - A second cache level (done)

- **The cache module** (`rtl/common/cache.sv`) gained four things, each a
  parameter that is off by default:
  - a line port (`LINE_PORT`): its upper side transfers whole lines with
    byte strobes, which is what the memory side of the cache above
    produces. The second-level cache is this module with that port, so
    every policy of Phase 8 is available at both levels independently;
  - a hit latency (`HIT_LATENCY`, and `HIT_JITTER` for verification);
  - inclusion (`INCLUSION`): before it evicts a line it takes the line back
    from the caches above, and a dirty copy from there replaces its own
    before the line is written back. The other half is in every cache: a
    port on which the cache below asks for a line to be given up;
  - a victim buffer (`VICTIM_ENTRIES`): a few lines found by full address
    that catch what the cache evicts; a miss on one of them is a swap that
    costs one cycle.
- **`rtl/common/line_arbiter.sv`** lets the two first-level caches share the
  L2: one request at a time, the cache that was not served last first.
- **`mem_subsystem`** has `L2_ENABLE` and the L2's parameters. With the L2
  off, the two caches have their own memory ports as before, and every
  existing table of [RESULTS.md](RESULTS.md) is unchanged, row for row.
- **Prefetchers** stay at the first level; their requests reach the L2 the
  way misses do.
- **The machine** for the measurements: the caches and predictor of `lru`,
  memory at 50 cycles per line, and an 8 KiB L2 (64 sets x 4 ways) that
  answers in 4 cycles. Five configurations (`h-l1`, `h-l2`, `h-incl`,
  `h-vb`, `h-pf`) and, for the victim buffer beside a direct-mapped cache,
  `dm-vb` next to the existing `dm` and `2way`.
- **Kernels:** `ws_walk`, which the bench stage also runs at eight
  working-set sizes from 256 bytes to 32 KiB (it rewrites two `.equ` lines
  of the one source file), and `incl_victim`.
- **Counters** per level, the cycles each cache kept a request waiting, and
  the cycles each first-level cache waited for the other's turn at the L2.
  RESULTS.md sets the measured time per data access beside the formula of
  lecture 21, `T1 = t1 + m1 x (t2 + m2 x tmem)`.

Verification added in this phase:

- Ten configurations per core (`l2-*`, `vb-*`): a one-line L2; an inclusive
  L2 of four lines under caches of eight; random L2 latency; write-through
  over write-back and the reverse; a write-through cache without a buffer
  over an inclusive L2; no first-level caches at all; victim buffers beside
  one-line, no-allocate and write-through caches and under an inclusive L2
  smaller than they are.
- `tb/hier_tb.sv`: both ports driven at once over the whole hierarchy, in 13
  configurations at three memory timings. Data against a byte array, the
  final contents of memory, the inclusion property, and each of the three
  caches' event logs against its own instance of the model. It also checks
  the two timings the results rest on, which the model cannot see.
- `tb/cache_tb.sv` runs ten more combinations: victim buffers, a cache that
  announces its evictions, and a process that asks for lines back at random
  moments as an inclusive cache below would.
- The model (`tools/cachesim.py`) knows the victim buffer, lines given up
  and lines taken back; 72 unit tests in all, 42 of them for the model.
- A directed program, `sw/tests/hier.S` (generated, 9 checks): several
  dirty lines at once, in different ways and sets, while the caches are
  made to need room at both levels.
- Two invariants inside the RTL, checked in every cycle of every run:
  inclusion (at the two moments it can break), and the arbiter never
  passing a waiting cache over twice.
- 34 planted bugs: 9 between the levels (line port, arbiter, latency), 15
  in inclusion, 10 in the victim buffer. Seven of them are seen by the
  inclusion invariant and by nothing else, two by the model alone, and two
  by the hierarchy testbench alone (VERIFICATION.md section 4).
- The regression makes a third model comparison per run where there is an
  L2 and has ten more configurations per core. To stay near half an hour,
  kernels outside the five measured machines now run a tenth at a time
  instead of a third; `--full` still runs everything everywhere. A run that
  stops advancing altogether is now ended after twenty minutes of real time.

Lectures 21, 23a and 23b were read again for this phase (23a's second half
repeats 23b, which is about several cores). What they say and the design
agree: first-level caches split and outer levels unified; the L2 asked only
after a first-level miss ("serial" access), with the cost the lecture names;
the first level acting as a filter on what the L2 sees; the three kinds of
hierarchy, with non-inclusive as the one that "relaxes design decisions";
the access-time recurrence `T_i = t_i + m_i x T_(i+1)` with local miss
rates; the victim cache as Jouppi's small fully associative buffer beside a
direct-mapped cache. Three things here go beyond them. How inclusion is
kept (taking lines back, and what happens to a dirty copy) is not in the
lectures, which give the property and one reason for it, "simplifies cache
coherence"; that reason only arrives with Phase 12. The lecture's warning
about the victim cache, "increases miss latency if accessed serially with
L2", does not apply: the buffer is searched in the same cycle as the cache.
And the lecture says "management policies are different across cache
levels"; here each level's policies are parameters, and the measured machine
uses LRU and write-back at both.

Things the phase found:

- **A loop with no register in it.** The first arbiter passed a request to
  the L2 in the cycle it arrived. In the in-order core the fetch request
  depends, within a cycle, on whether the data access was answered; through
  the arbiter the answer to the data access then depended on the fetch
  request. With first-level caches in between nothing happened. Without
  them the simulation stopped advancing, and the regression, which only
  limited cycles, waited for it indefinitely. The arbiter now registers
  every request (the L2's latency counts that cycle), and the regression
  limits real time as well.
- **A store caught between two levels.** A write-through store without a
  write buffer waits for the L2. If the L2 takes that very line back while
  the store waits, the store used to lose track of itself: its write
  arrived, was not recognised, and was sent again. No data was wrong; the
  model noticed the second write. The store now finishes when its answer
  comes, without its line.
- **A hole in the mutation check itself.** A planted bug that left memory
  unknown made the regression script crash on the checksum, and the
  mutation script read the dead script's silence as "no test failed". Both
  are fixed; the mutation check now refuses to go on if the regression does
  not finish.
- **Three results that were not planned for.** Inclusion was expected to cost a
  hot data line in the one kernel built to show it. In ordinary kernels its
  victim turned out to be the program's own loop, taken out of the
  instruction cache by the data streaming through the L2. The stride
  prefetcher, a plain gain since Phase 5, makes two kernels four to six
  times slower when every line they use falls into one set. And the
  access-time formula was wrong by a factor of two on `fill` until the L2's
  miss rate was counted the way the lecture defines it, over the lines the
  level above asked for (RESULTS.md, Phase 9).

Changes to the plan: `L2_HIT_LATENCY` cannot be 0 (above). `conflict` shows
the victim buffer on the `dm` machine, where its two arrays collide; on the
slow-memory machine, whose data cache has four ways, `ways_plus_one` and
`incl_victim` show it instead. A write that covers a whole line and misses
in an allocating L2 still reads the line from memory first; the counters
did not show that to matter: it happened six times in all 21 kernels
together.

Exit criteria met: on all three cores, in every configuration (thirty for
the in-order core, thirty-three and thirty-four for the out-of-order core
one and two wide), the commit trace and the final memory image equal the
golden model's, the event log of every cache agrees with the cache model
and no invariant fails, on every directed program, every kernel and 1,000
random programs (`python tools/regress.py --full --seeds 1000`: 315 of 315
groups, 1 hour 47 minutes); the default regression takes 25 minutes; the
cache testbench passes in 32 combinations and the hierarchy testbench in
13, at three timings each; the mutation check catches 198 of 198 planted
bugs; RESULTS.md has the staircase with its steps at 1 KiB and 8 KiB,
measured and computed access time side by side, and non-inclusive,
inclusive and victim-buffer columns for all 21 kernels.

### Phase 10 - Design-space sweeps (done)

`python tools/regress.py sweep` runs the kernels over a grid of cache
configurations and writes [SWEEPS.md](SWEEPS.md), its eight charts in
`docs/img/`, and the numbers behind both, `docs/data/sweeps.json`. With
`--tables-only` it rebuilds the document and charts from that file.

- **Two engines** (`tools/sweep.py`). The model replays a kernel's data
  accesses, recorded once from the golden model, through simulators of the
  cache: about five seconds for every configuration of every kernel. The
  RTL gives cycles, on a grid of 43 machines of the in-order core.
- **The two are tied together.** In each of the 903 RTL runs the reference
  model (`tools/cachesim.py`), run by itself from the golden model's
  accesses, must reproduce seven counters of the data cache exactly. The
  instruction side is replayed from the RTL's own log, because fetch also
  reads instructions down mispredicted paths that the golden model never
  sees. [VERIFICATION.md](VERIFICATION.md) section 5.
- **Sweeps:** miss rate against cache size (256 bytes to 16 KiB) for one,
  two, four and eight ways and fully associative; against line size (8 to
  128 bytes); each replacement policy against the optimal one (evict the
  line used furthest in the future, which only a model that knows the
  future can do); the four combinations of write policy; cycles against the
  size of the second-level cache (1 to 32 KiB), inclusive and not; the
  instruction cache.
- **Miss classification** with the definitions of DDCA lecture 22:
  compulsory, capacity (what a fully associative cache of the same size
  with optimal replacement also misses) and conflict (the rest), per kernel
  and associativity.
- **Charts:** `tools/svgplot.py`, plain Python writing SVG, with a light and
  a dark set of colours in each file. `tools/figures.py` draws the charts of
  the earlier results from `docs/data/project.json` (the counters of the
  last bench run, the report of the last mutation check, and the size of
  the project at each phase), and nine diagrams at three levels: the whole
  machine; the two cores, the memory hierarchy and the checks; and, inside
  the blocks, one cache access, the predictor, the life of an instruction
  and the second slot of the two-wide core. RESULTS.md, ARCHITECTURE.md and
  VERIFICATION.md show them.
- **The line size became a real parameter of the measured machines.** It had
  been a parameter of the RTL since Phase 3 and 32 bytes in every
  configuration; the sweep runs 8, 16, 64 and 128 as well.

No RTL changed in this phase.

Verification added in this phase:

- The comparison of the model on its own with the RTL, above: 777 runs on
  the data side (every run without an inclusive second level) and 903 on
  the instruction side.
- Unit tests of the analyses on random traces (`tools/tests/test_sweep.py`): the
  fast LRU simulator equals the reference model; the optimal policy never
  misses more than LRU, FIFO, random or pseudo-LRU, and equals an exhaustive
  search over every choice of victim on small traces; a larger fully
  associative cache never misses more than a smaller one; the three classes
  add up to the misses.
- Tests that the generated files are up to date, as for `pairs.S`: the
  document and every chart are what the committed numbers produce
  (`tools/tests/test_sweep.py`, `tools/tests/test_figures.py`), the numbers belong to
  the kernels as they are now (a checksum of each source), and the model
  reproduces a sample of them.
- Every run of the grid is also checked as in the `rtl` stage: commit
  trace, final memory image, and each cache's event log.

Things the phase found (RESULTS.md, Phase 10, has the numbers):

- **More ways, more misses.** Nine of the fifteen kernels have a step where
  the same size in more ways misses more often, and a kernel with a 64-byte
  stride misses on every access in a cache as large as its data at every
  associativity up to eight.
- **LRU behind random.** All kernels together, random replacement misses
  less often than LRU at every size from 512 bytes to 8 KiB.
- **The curve of miss rate against line size never turns** on these
  kernels, which is a limit of the kernels and of a memory that takes the
  same time for a line of any size.
- **Inclusion is a cost of small second levels**, 12% to 15% up to 8 KiB and
  nothing from 16 KiB, and at 1 KiB the inclusive cache is the faster one
  on five kernels: a limit of the L2 that Phase 9 recorded as invisible
  (a whole-line write that misses reads the line first) happens 5,562 times
  there instead of 6.
- **The first failure was in the comparison, not in the RTL.** Its first complete run
  failed on the instruction side of every inclusive machine. The replay did
  not know about lines taken back by the second level. The RTL was right,
  and every other check of those runs had passed.

Changes from the plan: the instruction side is tied to the RTL's fetch log
at every run, not at sampled points; the sweep of the instruction cache is
small, because every kernel but `code_walk` has at most nine lines of code;
the model's sweeps cover the data cache alone, without a second level, a
victim buffer or prefetchers, which stay with the RTL.

Exit criteria met: `python tools/regress.py sweep` writes the document and
its charts (44 of 44 groups, 26 minutes); the model and the RTL agree
exactly in all 903 runs; RESULTS.md has, for each sweep, what it shows and
where the textbook expectation did not hold; the default regression still
passes (315 of 315 groups).

This was the last phase. The out-of-order improvements and the two
multicore phases that were planned after it are in section 6.

## 5. Benchmark kernels

The kernels in `sw/bench/` are the project's pitch. Each is a small
self-checking program built so that one component makes a visible difference
to it, and several are built so that a component makes *no* difference, which
is as informative. `python tools/regress.py bench` runs every kernel on every
configuration and regenerates the table in
[RESULTS.md](RESULTS.md). Because each feature can be switched off by a
parameter, earlier columns are re-measured rather than copied forward.

| Kernel | Shows | Expected effect |
| --- | --- | --- |
| `loop_nest` | Branch prediction | Loop branches are almost always taken; BTB + 2-bit counters remove nearly every flush that predict-not-taken pays. |
| `branch_alt` | Limit of a 2-bit counter | A branch that alternates taken/not-taken defeats a 2-bit counter. Shown deliberately. |
| `calls` | BTB for jumps | Calls and returns stop costing a flush once their targets are cached. |
| `bsearch` | Data-dependent branches | Hard-to-predict branches bound what any predictor can do. |
| `array_sum` | Cache, next-line and stride prefetch | Sequential walk: one miss per line with a cache, almost none with a prefetcher. |
| `stride_walk` | Stride prefetcher | Touches one element per cache line, so every access misses unless the stride is learned. |
| `list_walk` | Limit of prefetching | Shuffled linked list: no stride to learn, and each miss depends on the previous one. |
| `conflict` | Associativity | Two arrays that map to the same lines of a direct-mapped cache thrash it; a 2-way cache holds both. |
| `ilp_chains` | Out-of-order and superscalar | Several independent dependency chains: a wide out-of-order core runs them side by side. |
| `dep_chain` | Limit of out-of-order | One long serial chain: no parallelism to find, IPC stays near 1. |
| `miss_indep` | Out-of-order under cache misses | Work that does not depend on a missing load proceeds while the miss is served. |
| `code_walk` | Instruction cache and instruction prefetch | Straight-line code larger than the instruction cache: one miss per line unless the next line is fetched ahead. Generated by a script. |
| `memcpy`, `sieve`, `matadd` | General | Ordinary code, to show the overall trend. |
| `lru_fifo` | LRU against FIFO | Three lines are used every round and a fourth place is shared by lines used once each. LRU keeps the three; FIFO evicts them on schedule. |
| `ways_plus_one` | Limit of LRU | Cycles over one line more than the set has ways (the example of DDCA lecture 22). LRU and FIFO miss every time; random misses 40%. |
| `fill` | No-allocate, merging | Writes a large array and hardly reads it. Allocating reads every line from memory only to overwrite it; not allocating sends each store by itself unless the write buffer merges them. |
| `store_hot` | Write-back, write buffer | A histogram: thousands of stores to two lines. Write-back sends nothing to memory; write-through sends every store, and without a buffer waits for each. |

The table reports, per kernel and configuration: instructions, cycles, IPC,
and from the relevant phase onward branch mispredictions and cache misses.

| `ws_walk` | Both cache levels | The same loop over working sets of growing size (the bench stage runs it at eight sizes). Time per load steps up when the working set outgrows the first-level cache and again when it outgrows the second. |
| `incl_victim` | Cost of inclusion, victim buffer | A line used constantly in the data cache is never seen by the L2 again. An inclusive L2 evicts it and takes it out of the data cache; a non-inclusive one leaves it there. A victim buffer serves the remaining misses in one cycle. |

`lru_fifo`, `ways_plus_one`, `fill` and `store_hot` belong to Phase 8 and
are measured on the cache-policy configurations only; `ws_walk` and
`incl_victim` belong to Phase 9 and are measured on the hierarchy's. The
tables of the first stage keep the fifteen kernels they were built on.

## 6. Future work

### Planned and not built

The second stage was planned to run to Phase 13 and was closed after Phase
10. What follows is the plan for the three phases that were not built, as it
stood when the project ended: the design, the checks and the exit criteria
each would have had. Nothing in this subsection exists in the repository.
It is kept because the thinking is done, and because each phase was planned
against the machine that does exist: every one of them starts from a
measured weakness in [RESULTS.md](RESULTS.md).

Phase 11 does not depend on the others. Phase 13 needs Phase 12.

Design choices proposed for these phases:

| Topic | Choice | Why |
| --- | --- | --- |
| Cores in the multicore | The in-order core, two harts by default, four possible. | Its loads and stores happen in program order; the out-of-order core runs loads early, which raises memory-ordering questions that are a project of their own. |
| Coherence | MSI on a bus that carries one transaction at a time; MESI as an option. | The smallest real protocol, with the fewest in-between states. |
| Atomic instructions | The atomic memory operations (`amoswap`, `amoadd` and the rest) for 32 and 64 bits. `lr`/`sc` only if time allows. | Enough for locks and shared counters without reservation tracking. |

#### Phase 11 - Making out-of-order pay

Out-of-order execution at width 1 is worth 1.03x over the in-order core
(RESULTS.md). [ARCHITECTURE.md](ARCHITECTURE.md) section 10 names the
reasons, and this phase removes three of them. Each is a parameter, so each
is measured on its own, and with all three off the core is the Phase 7 core
cycle for cycle.

- **`EARLY_RECOVERY`.** A mispredicted branch redirects fetch when it
  executes instead of when it retires. Everything younger is removed from
  the ROB, the reservation stations and the queue, and the RAT is rebuilt
  from the ROB entries that remain. Exceptions are still taken only at the
  head. At width 2 the older of two mispredictions in a cycle wins.
- **`STL_FORWARD`.** A load takes its data from the youngest older store
  that covers all the bytes it reads. A partial overlap waits as today.
  Whether the load faults is still decided by its address alone. Loads
  still wait until every older store address is known.
- **`MISS_SLOTS`.** The data cache can have several lines on their way at
  once, and the queue lets a load to another line go ahead while one waits.
  The memory model and the L2 accept several outstanding requests.
- **If time allows:** a second read port on the data cache; letting loads
  pass stores whose address is not yet known, with a flush when that was
  wrong.
- **Pipeline viewer.** With `+konata=<file>` the testbench writes a log for
  the Konata pipeline viewer, showing each instruction's path through fetch,
  dispatch, execution and retirement, and what was squashed. The format is
  to be taken from the viewer's documentation when this is built. It is a
  presentation aid and is outside the regression apart from a check that
  the file is produced.

Kernels: `branch_rand`, `stl_chain`, `miss_pair`, and the existing
`miss_indep` and `list_walk`.

Verification specific to this phase:

- A generated directed test (`sw/tests/gen_fwd.py`): every store size
  against every load size at every offset, for full cover, partial overlap
  and no overlap, at each pair placement.
- A memory-heavy profile for `rvgen.py` with a small pool of addresses, so
  that loads and stores collide constantly.
- New verification configurations: each feature alone and all together on
  the smallest windows, with garbage predictions (wrong-path loads and
  stores are now removed from the middle of the ROB) and random latency.
- Invariants: a removed entry never retires; the RAT after a recovery
  equals the RAT rebuilt from scratch; no load completes twice.
- About 35 planted bugs.

Exit criteria: three cores, golden trace and memory image equal everywhere;
every planted bug caught; a table with each feature alone and all together,
on the Phase 7 machine and on the Phase 9 machine; each feature faster on
the kernel built for it, and any kernel that gets slower explained.

#### Phase 12 - Several cores: coherence

The first of two multicore phases. In this one the cores share cache lines
but never bytes, which keeps the existing rule (each trace equals the golden
trace exactly) while the coherence protocol does all of its work.

- **Testbench and platform.** A `HARTS` parameter (1, 2 or 4) on the
  in-order core. Each hart has its own `mhartid`, starts at the reset
  address and branches on its id. Each has its own commit trace and halts
  on its own; the run ends when all have.
- **Memory system.** Each hart has its own L1 instruction and data caches
  and prefetchers. All share the L2 and one bus.
- **Coherence.** Each L1 data line is invalid, shared or modified. A cache
  that wants to read a line it does not have, or write a line it does not
  own, asks on the bus; the bus carries one such request at a time; the
  other caches see it and give the line up or step down to shared, and a
  cache holding the line modified supplies the data. `COHERENCE` selects
  MSI or MESI (a line read by one cache alone can later be written without
  asking).
- **Golden model.** `rvsim.py --harts N`. Because the harts write disjoint
  bytes, each is simulated on its own and no order between them matters.
- **Random programs.** `rvgen.py --harts N` writes N programs whose data
  lies in the same lines: hart *h* owns every N-th doubleword. Stacks and
  trap handlers are per hart.

Kernels: `par_sum` on 1, 2 and 4 harts; the twins `false_share` and
`no_share`; `bus_bound`.

Verification specific to this phase:

- Each hart's trace equals its golden trace, and the final memory image
  equals the golden one, in every configuration: tiny caches with random
  latency so that lines move between harts constantly, each write policy,
  both inclusion settings, MSI and MESI, two and four harts.
- Invariants in the testbench: a line is modified in at most one cache; a
  modified line is in no other cache; all shared copies of a line are
  equal.
- The cache model is extended with the line states and replays the bus log,
  so a wrong but harmless state change is caught too.
- About 30 planted bugs: an invalidation not sent or ignored, a modified
  copy not supplied, a write allowed on a shared line, two requests on the
  bus at once.

A stale copy does not need to be read to be caught: when it is evicted it
is written back as a whole line and overwrites the other hart's bytes.

Lecture 23b is to be read first. The protocol itself is outside the DDCA
series; the source used is to be chosen then and named here.

Exit criteria: all of the above passing with two and four harts; speedup
of `par_sum` against the number of harts in the results; `false_share`
against `no_share` with the number of invalidations beside the cycles; the
single-hart machine unchanged cycle for cycle.

#### Phase 13 - Several cores: atomics and shared data

- **Instructions.** The atomic memory operations for 32 and 64 bits in the
  assembler, the golden model, the decoder and the in-order core. The core
  gets the line modified and keeps it for the read and the write, so
  nothing can come between them.
- **Checking shared data.** Harts now read what other harts wrote, so the
  result depends on who got there first. The RTL logs the order in which
  data accesses took effect, and the golden model executes the harts in
  that order. Each hart's commit trace and the final memory image must then
  match as before. The order is well defined because a line can be written
  in only one cache at a time.
- **Directed programs,** also self-checking: a counter under a spinlock, a
  counter with `amoadd`, a ring buffer handed between two harts, a parallel
  sum with a shared total.
- **Random programs** with a shared region, reached through atomic
  operations and through plain loads and stores, races included.

Kernels: `lock_tas` against `lock_ttas`, `amdahl`, `true_share`.

Verification specific to this phase: the order log is checked for
consistency before it is used (no two writers of a line in one cycle, each
hart's entries in its own program order); about 20 planted bugs, among them
an atomic operation that lets the line go between its read and its write,
which must be caught by the counter programs and by random programs.

Exit criteria: all programs matching with two and four harts in every
configuration; every planted bug caught; lock contention, the Amdahl curve
and true against false sharing in the results.

The out-of-order core is not part of the multicore. It reads memory early,
and making that correct when another hart stores in between needs loads to
be re-executed; the list below has it.

#### Kernels planned for these phases

As before, several are built to show a component *not* helping.

| Phase | Kernel | Shows | Expected effect |
| --- | --- | --- | --- |
| 11 | `branch_rand` | Early recovery | Branches on pseudo-random data. Each misprediction costs less when it is repaired at execution. |
| 11 | `stl_chain` | Store-to-load forwarding | A value carried through memory from each iteration to the next. |
| 11 | `miss_pair` | Overlapping misses | Two independent linked lists walked together: their misses can overlap. Its twin `list_walk` has one list and cannot gain. |
| 12 | `par_sum` | Parallel speedup | An array summed in 1, 2 or 4 parts. |
| 12 | `false_share`, `no_share` | False sharing | Two harts each increment their own counter. In one line the line moves on every store; in two lines it never moves. |
| 12 | `bus_bound` | Limit of adding cores | Every hart misses constantly; the shared bus, not the cores, sets the pace. |
| 13 | `lock_tas`, `lock_ttas` | Lock design | Testing the lock with a plain load before the atomic swap avoids taking the line from the holder on every attempt. |
| 13 | `amdahl` | Amdahl's law | A fixed serial part and a parallel part (generated for several ratios): speedup flattens at one over the serial share. |
| 13 | `true_share` | True sharing | Both harts update the same counter atomically: the line must move, however the data is laid out. |

### Out of scope

Deliberately left out of the plan altogether, in rough order of how
naturally they would follow. The first four are small and could be slotted
in before any of the phases above:

- Multiply/divide (M extension): a multi-cycle unit, straightforward once
  Phase 6 exists; it would allow kernels such as matrix multiply.
- A return-address stack; `calls` would show it.
- A wider front end: a fetch queue and pairs that cross a cache line.
- A prefetcher at the L2, and different line sizes at different levels.
- Memory-dependence prediction, beyond the simple form listed under
  Phase 11 above.
- History-based predictors (gshare, TAGE).
- An exclusive cache hierarchy.
- The out-of-order core in the multicore: loads that ran early must be
  repeated when another hart writes the line before they retire.
- `lr`/`sc`, unless Phase 13 has room for them.
- Interrupts: asynchronous events need an injection mechanism in the golden
  model to stay comparable.
- Virtual memory (lecture 24): TLBs, page-table walks, supervisor mode.
- Compressed instructions.
- The official RISC-V test suites: they need a GNU toolchain, which the
  tools decision in section 3 rules out.
- Synthesis, FPGA implementation and timing closure.
