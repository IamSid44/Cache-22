# Verification strategy

Every processor in this repository is checked the same way: it runs a
program, reports each instruction it retires, and that report must equal the
one produced by a simple reference simulator. This document defines the
reference, the report format, the tests, and how to investigate a failure.

Two further comparisons are made on every run. The final contents of memory
must equal the reference simulator's (section 3). And each cache must have
behaved as its policies say, which no program result can show; a second
reference, a model of the cache, checks that (section 5).

![How every run is checked](img/verification.svg)

## 1. Running the regression

```text
python tools/regress.py                    # unit, sw, blocks and rtl stages
python tools/regress.py rtl --seeds 1000   # one stage, with more random programs
python tools/regress.py rtl -k hazards     # tests whose name contains "hazards"
python tools/regress.py rtl -c dm -v       # one configuration, every program listed
python tools/regress.py rtl --core ss      # one core: inorder, ooo or ss
python tools/regress.py bench              # benchmark kernels, cycle counts
python tools/regress.py sweep              # design-space sweeps; the cache model against the RTL
python tools/mutation.py                   # can the tests see a broken core?
```

The default run takes about 25 minutes; `--core`, `-c` and `-k`
narrow it while debugging.

The regression knows three cores. `inorder` is the 5-stage pipeline. `ooo`
and `ss` are the same RTL, the out-of-order core, built one instruction wide
(scalar) and two wide (superscalar).

Requires Python 3.10+ and Icarus Verilog (`iverilog`, `vvp`) on the PATH.
Generated files go to `build/`. The exit status is non-zero on any failure.

| Stage | What it checks |
| --- | --- |
| `unit` | Assembler encodings and golden-model behaviour (`tools/tests/test_tools.py`); the random-program generator; the cache model; the sweep analyses, and that `docs/SWEEPS.md`, the charts in `docs/img/` and the numbers in `docs/data/` belong together (`tools/tests/test_sweep.py`, `tools/tests/test_figures.py`) |
| `sw` | Each program in `sw/tests/` and `sw/bench/` assembles, runs on the golden model, and exits with code 0 |
| `blocks` | The cache alone, in 32 combinations of shape and policy (and once switched off) at three memory timings: its data against a byte array, the final contents of memory, and its event log against the cache model (`tb/cache_tb.sv`, `tools/cachesim.py`); the cache hierarchy, in 13 configurations at the same timings: data on both ports, the final contents of memory, the inclusion property, two timings, and the event log of each of the three caches against its own model (`tb/hier_tb.sv`); the branch predictor's policy, in four configurations (`tb/bpred_tb.sv`); the prefetcher's policy, in four (`tb/prefetcher_tb.sv`) |
| `rtl` | For each of the three cores, on every directed program and benchmark kernel under every machine configuration that applies to it (section 5: thirty for the in-order core, thirty-three for the out-of-order core at width 1, thirty-four at width 2), and on `--seeds` random programs (default 100) spread across them: the commit trace equals the golden model's, the final contents of memory equal the golden model's, and each cache's event log agrees with the cache model. Kernels run in full on the five machines they are measured on and a tenth of them in each other configuration; `--full` runs all of them everywhere |
| `bench` | Runs the benchmark kernels on the eleven benchmark configurations, the eleven cache-policy configurations and the six of the hierarchy, and `ws_walk` at eight working-set sizes, and tabulates the results; every run is checked as in the `rtl` stage; not part of the default run |
| `sweep` | Runs every kernel on a grid of 43 machines of the in-order core (data cache size and associativity, line size, replacement policy, write policy, instruction cache size, second-level cache size and inclusion); every run is checked as in the `rtl` stage, and in addition the cache model, run on its own, must reproduce the RTL's cache counters exactly (section 5); writes `docs/SWEEPS.md` and its charts; not part of the default run |

To work with one program by hand:

```text
python tools/rvasm.py sw/tests/sort.S --lst build/sort.lst -o build/sort.hex
python tools/rvsim.py sw/tests/sort.S --trace build/sort.trace --regs
python tools/rvgen.py --seed 7 -o build/seed_7.S
python tools/cachesim.py build/sort.dc.log --sets 4 --ways 2 --repl 1
```

The RTL testbench (`tb/core_tb.sv`) is built by the `rtl` stage once per
configuration, as `build/rtl/<core>-<configuration>.vvp`, and can be run
directly; `+vcd=` dumps a waveform:

```text
vvp -n build/rtl/inorder-dm.vvp +hex=build/golden/tests/sort.hex +mem_latency=10 +trace=build/sort.trace +vcd=build/sort.vcd
```

`+dcache_log=` and `+icache_log=` write the caches' event logs, and
`+memdump=` the final contents of memory.

## 2. The platform

The golden model and every core implement this machine. Where the RISC-V
specification leaves a choice, the choice is listed here.

| Item | Definition |
| --- | --- |
| ISA | RV64I, machine mode only |
| Reset | `pc = 0`, all registers 0, `mstatus = 0x1800`, `mtvec = 0` |
| RAM | 64 KiB at address 0, little-endian; program image loaded at 0 |
| Console | A store to `0x1000_0000` prints the low byte |
| Halt | A store to `0x1000_0008` ends the run; the stored value is the exit code (0 = pass) |
| Misaligned load/store | Traps (cause 4 or 6), `mtval` = address |
| Access outside RAM | Traps (cause 5 or 7; cause 1 for fetch), `mtval` = address |
| Misaligned jump target | Traps on the jump or taken branch (cause 0), `mtval` = target; the link register is not written |
| Illegal instruction | Cause 2, `mtval` = the instruction word |
| `ecall` / `ebreak` | Cause 11 / cause 3 (`mtval` = pc) |
| Trap entry | `mepc` = pc of the trapping instruction, `MPIE` = `MIE`, `MIE` = 0, `pc = mtvec` (direct mode only) |
| `mret` | `pc = mepc`, `MIE` = `MPIE`, `MPIE` = 1 |
| CSRs | `mstatus` (MIE, MPIE; MPP fixed at 3), `mtvec`, `mepc`, `mcause`, `mtval`, `mscratch`, `mie`, `mip`, `misa` (read-only), `mcycle`, `minstret`, and the four ID registers (read as 0). Any other CSR, or a write to a read-only one, is an illegal instruction. |
| `fence`, `wfi` | No-ops |
| Interrupts | None |

`mcycle` depends on timing, so the golden model cannot predict it; programs
run under trace comparison must not read it.

## 3. The commit trace

One line per event, in program order:

```text
C <pc> <instruction> [x<rd>=<value>] [m[<address>,<bytes>]=<value>]
T <pc> <instruction> cause=<n> tval=<value>
```

`C` is a retired instruction with the register and memory it changed (a write
to `x0` is not shown). `T` is an instruction that trapped instead of retiring.
All numbers are zero-padded hex except the register number, byte count and
cause.

```text
C 0000000000000008 00c5b423 m[0000000000000108,8]=fffffffffffffffe
C 000000000000000c 0085b683 x13=fffffffffffffffe
C 0000000000000010 00d60463
T 0000000000000018 ffffffff cause=2 tval=00000000ffffffff
```

The rule: **the RTL's trace must equal the golden model's, line for line.**
The RTL produces its trace from signals at the point of commit (writeback in
the in-order core, the reorder-buffer head in the out-of-order core). The
two-wide core can retire two instructions in one cycle; the testbench writes
the older one's line first, so the trace has the same form at any width.
This single rule covers a great deal:

- a wrong result, a wrong branch, or a missed forward shows up as a differing
  line;
- a speculative instruction that leaks into architectural state shows up as
  an extra line;
- an imprecise exception shows up as a `T` line in the wrong place, or as a
  `C` line after it that should not exist;
- out-of-order commit shows up as reordered lines.

What the trace does not show is timing. Performance is tracked separately
with counters (section 6).

### The final memory image

The trace shows a store's value at the moment it commits. Whether that value
is still in memory at the end of the run is a different question, and the
trace only answers it if a later load happens to read the place back. A
write buffer that drops an entry, or a dirty line that is evicted without
being written, can lose a store that nothing reads again.

So when a run ends the testbench works out what memory holds: the memory
model's contents with everything the data cache has not yet sent laid over
them, oldest first (a write-back in progress, the writes in the write
buffer, the dirty lines, including those in the victim buffer). With a
second-level cache the same is done for it first and for the data cache on
top, because the data cache's copy of a line is the newer one. It prints a
checksum of that image, and the
regression compares it with the checksum of the golden model's memory. They
must be equal, for every program on every core in every configuration. If
they are not, the regression runs the program once more with `+memdump=` and
reports the first byte that differs.

## 4. Tests

**Directed programs** (`sw/tests/`). Self-checking assembly: `gp` holds the
number of the check in progress and becomes the exit code on a mismatch, so a
failure names the check even without a trace. Current set:

| Program | Covers |
| --- | --- |
| `arith.S` | add/sub, logic, comparisons, `lui`, `auipc`, wide constants, the `W` arithmetic ops |
| `shifts.S` | all shifts, 64-bit and 32-bit, shift-amount masking |
| `branch.S` | all six branches both ways, `jal`, `jalr`, loops, recursion with a stack |
| `mem.S` | every load and store width, sign and zero extension, byte order, narrow stores |
| `traps.S` | that each exception is reported correctly (cause, `mepc`, `mtval`), `mret`, CSR instructions, `mstatus` stacking |
| `precise.S` | that exceptions are precise: an instruction behind a faulting one (a store, a register write, a CSR write) never takes effect; an older late fault beats a younger early one; squashed faults do not trap; fetch outside RAM; `minstret` |
| `hazards.S` | one check per forwarding, stall and squash path of a pipeline |
| `ooo.S` | cases that only matter out of order: a load behind a store whose address or data is late, partial overlaps, a renamed register with a reader still waiting, wrong-path work that must leave no trace, an older late fault against a younger early one |
| `pairs.S` | cases that only matter two instructions at a time (below) |
| `wpolicy.S` | stores that have to survive the cache's write policy (below) |
| `hier.S` | several dirty lines at once that have to survive a cache hierarchy (below) |
| `predict.S` | control flow that a predictor gets wrong: each check trains it one way and then does the other (direction both ways, changing targets, around loads, stores and traps, two branches sharing an entry) |
| `sort.S`, `fib.S` | small kernels: data-dependent branches, load/store traffic, console |

`pairs.S` is generated by `sw/tests/gen_pairs.py`. The two-wide core treats
two neighbouring instructions as a pair, and what can go wrong is specific
to the pair: the second reading a register the first writes, both writing
one register, the first trapping or branching away with the second already
beside it, a store and a load of the same place side by side, a CSR
instruction next to the instruction that produces or uses its value, two
retirements counted in `minstret`. The file holds 35 such sequences. Which
instructions are paired depends on where they lie: fetch starts a new pair
at the target of a taken jump, and a pair never crosses a 32-byte cache
line. So every sequence appears three times, each time behind a jump to the
start of a line, with 0, 1 or 7 `nop`s in front: its first two instructions
form a pair, or its second and third do, or its first is fetched alone at
the end of a line. That gives 105 checks, each with its own number.

`wpolicy.S` is generated by `sw/tests/gen_wpolicy.py`. With write-back and
no write buffer a store has one way to go wrong on its way to memory. The
other write policies add more: a store can be queued in the write buffer
while its line is evicted, wanted again or written again; it can have been
merged with other stores; under no-allocate it never has a line in the cache
at all; and it can meet a line that a prefetcher has just asked for. The
file holds 18 checks. Each stores to lines no earlier check touched and
reads the bytes back, at once and again after reading five lines 4096 bytes
away, which pushes the stored line out of any small cache. Two checks are
loops with only two instructions between stores, one store per line: that
is the spacing at which a store finds the next line absent but already on
its way from a next-line prefetcher. Expected values come from a byte array
the generator keeps, not from running the program.

`hier.S` is generated by `sw/tests/gen_hier.py`, with the same byte array.
`wpolicy.S` puts one stored line at a time under pressure. With a second
level, and above all an inclusive one, what matters is several dirty lines
at once and where they are: the L2 takes a line back from the first level
whenever it needs the room, from whichever way of whichever set the line is
in, whatever the processor is doing at that moment. The file holds 9 checks.
Each makes several lines dirty with different data, makes the caches need
room by reading other lines that share their sets (4096 bytes apart, the
same set in any small cache; 64 bytes apart, different sets of a small
first-level cache and one set of a smaller L2), and reads everything back.
The same sequences are what a victim buffer catches: lines that leave the
cache dirty and are wanted again a moment later.

**Random programs** (`tools/rvgen.py`). Directed tests only find the bugs
someone thought of. The generator turns a seed into a program of about 300
static instructions (about 1,300 executed) that is safe by construction: in
its plain mode it never traps, and it always terminates. It is aimed at
pipeline corner cases
rather than typical code:

- a pool of only eight working registers, so neighbouring instructions
  depend on each other;
- a load immediately followed by a use of the loaded value as either
  operand, as store data, as an address, as a branch operand or as a jump
  target;
- a store immediately followed by a load of the same bytes, at the same or a
  different width;
- taken branches and jumps whose shadow holds stores and register writes
  that would visibly corrupt state if they were not squashed;
- `jalr` with odd offsets, with `rd == rs1`, and with a target computed one
  instruction earlier; calls to small leaf functions; bounded loops.

With `--traps` (odd seeds in the regression) the program also installs a
trap handler and mixes in faults and CSR traffic, about 2,300 instructions
and 100 traps per program:

- every exception the platform defines except a fetch outside RAM, each
  placed next to the instructions most likely to expose a mistake: right
  after the load that produced its operand, in the shadow of a taken branch
  (where it must not trap), back to back, and an older late fault followed
  by a younger early one;
- a handler that folds `mcause`, `mtval` and `mepc` into a checksum
  register, so wrong trap bookkeeping changes the trace;
- the skip mechanism: before some faults the program arms a register that
  makes the handler resume one instruction further on. The skipped
  instruction is a store or a register or CSR write that is then never
  executed, so if its effect appears the pipeline leaked it;
- CSR reads and writes with pool registers, including a CSR read used by the
  next instruction and a write read back at once; `minstret` reads and
  writes. `mcycle` is never touched.

Its own unit tests (`tools/tests/test_rvgen.py`) check, over 150 seeds in each
mode, that every program runs clean on the golden model and only stores into
its scratch area; that every RV64I instruction is executed and every branch
type is seen both taken and not taken; that all eight generated exception
causes and all six CSR instruction forms occur; and that plain-mode output
for a given seed never changes. A seed that exposes an RTL bug is kept as a
permanent test in `sw/tests/`.

**Benchmark kernels** (`sw/bench/`). Larger self-checking programs written to
measure performance (HISTORY.md section 5). They are also correctness tests:
the `rtl` stage compares their traces like any other program.

**Microarchitecture-directed programs.** Each phase adds programs aimed at
its own corner cases, for example: back-to-back load-use, a branch in the
shadow of a mispredicted branch (`predict.S`), a load behind a store whose
address is late (`ooo.S`), a store followed by a load to the same address in
the same pair and a trap on the second instruction of a pair (`pairs.S`).

**Unit testbenches.** Blocks with internal state that is hard to reach from
a program (cache, predictor, reorder buffer, store queue) get their own
testbench with a simple behavioural model.

The first is `tb/cache_tb.sv`. It drives the cache with 20,000 random loads
and stores of every size over a region much larger than the cache, checks
every value read against a byte array that mirrors the writes, and finally
reads the whole region back. Because the region does not fit, lines are
evicted constantly, so a dirty line has to survive a write-back and a later
refill to be read correctly. It also abandons reads that are still waiting,
as fetch does when a branch redirects it, and throughout the run a separate
process fires prefetch requests for random lines at random moments. Directed
checks cover the prefetch port itself: a prefetched line is there without a
wait, is counted as useful once, is not fetched again if present, and does
not make a hit on another line wait. At the end it lets every write that is
on its way arrive and compares memory itself, with the dirty lines laid over
it, against the byte array; under write-through nothing may be dirty, so
memory alone has to be right.

One thing cannot be seen in the data at all: a wrong replacement order, or a
write sent to memory that did not need to be, still returns correct values.
For that the cache writes its event log and the regression replays it
against the cache model (section 5), for every one of the 32 combinations:
each replacement policy at several shapes, write-through and no-allocate
with and without a write buffer, merging, and write-back through a buffer;
and, since Phase 9, a victim buffer beside several of those, a cache that
announces its evictions as an inclusive one does, and runs in which a third
process plays an inclusive cache below: at random moments it asks for a
random line back, and writes the line to memory itself if it comes back
dirty. The testbench also still checks the LRU order directly, as it did
before the model existed, by filling a set, touching one line, forcing an
eviction, and timing which line is still there.

`tb/hier_tb.sv` tests the hierarchy as a whole: the memory subsystem with
both first-level caches, the arbiter and the second-level cache. Two
processes drive the two ports at the same time, as a core does. One fetches
from the lower half of a small region and now and then abandons a fetch
that is still waiting; the other loads from the whole region and stores to
its upper half. (Stores stay out of the half that is fetched from, because
the instruction cache's copy of a line is never updated: self-modifying
code is outside what this memory system supports.) Loads from the lower
half make both first-level caches hold the same lines, so an inclusive L2
has to take a line back from both. Every value read is checked against a
byte array. At the end everything comes to rest, what the caches hold is
laid over memory, and memory must equal the byte array; under inclusion
every line of a first-level cache or victim buffer must then be in the L2.
Each of the three caches writes its event log, and each log is replayed
against its own instance of the model. The 13 configurations include an L2
smaller than the caches above it, a one-line L2, every pairing of
write-back and write-through between the levels, victim buffers under an
inclusive L2, no first-level caches at all, and no L2.

The model has no clock, so the two timings the results rest on are checked
here directly, in the plain configurations: a miss that the L2 serves waits
the L2's latency plus two cycles, and one that goes to memory waits the L2's
latency, memory's, and four more.

The second is `tb/bpred_tb.sv`, for the same reason taken further: nothing
about a predictor's quality is visible in program results, because the
pipeline corrects every wrong prediction. The testbench checks the policy
with directed sequences: no entry for a branch that is never taken, a
prediction after the first taken outcome, the target following the latest
one, hysteresis and saturation of the counter, one branch taking over
another's entry, and aliasing with short tags. It also checks the two
numbers DDCA lecture 15b gives: a loop branch is mispredicted once per run
with a 2-bit counter and twice with one bit, and an alternating branch is
half wrong with two bits and always wrong with one. The predictor's second
lookup and training ports, which only the two-wide core uses, are checked
too: both lookups see the same table, both training ports work in the same
cycle, and when both train one entry the younger instruction's update is the
one that stays.

The third is `tb/prefetcher_tb.sv`, on the same principle: what a prefetcher
proposes cannot change any result, so only a direct check can test it. It
feeds sequences of accesses and checks the proposal after each one: the next
line; for the stride policy, no proposal until the stride has repeated
twice, then address + stride x distance; a negative stride; two
instructions tracked separately; nothing for an irregular pattern or a
repeated address; an immediate restart after the jump back at the end of a
pass; a changed stride learned without ever proposing the old one in
between; and an instruction that takes over another's table entry without
inheriting its pattern.

![Planted bugs and what noticed them](img/planted_bugs.svg)

**Mutation check** (`tools/mutation.py`). A regression that always passes
proves nothing unless it can fail. This script plants 198 realistic bugs in
the RTL one at a time, runs the directed and random programs against each
under up to fourteen machine configurations, and reports any bug that no
test noticed. A bug in the out-of-order core is tried at width 2 and, if
nothing notices it there, at width 1, because a few bugs only matter at one
of the two. All others, including those in blocks the cores share, are run
on the in-order core. Bugs planted in the cache, the cache hierarchy, the
branch predictor or the prefetcher are also run against that block's
testbench.

For each bug the report says which checks failed in the program runs: the
commit trace, the final contents of memory, the cache model, or an
invariant inside the RTL. That is how the groups of cache bugs below are
told apart.

| Group | Count | Examples |
| --- | ---: | --- |
| Pipeline | 14 | a missing forwarding path, a missing squash, a load-use stall that ignores one operand, a wrong sign extension |
| Exceptions and CSRs | 16 | a trap that does not squash the instruction behind it, a branch redirect that beats a trap, a faulting store that still writes, a trap counted as a retirement |
| Memory stalls | 6 | a stage that keeps moving during a data miss, a held instruction reported twice, no bubble while fetch waits |
| Cache | 8 | a store that does not mark its line dirty, a write-back to the wrong address, a lookup that ignores the tag or the valid bit, the wrong line evicted |
| Misprediction recovery | 6 | fetch ignoring the prediction, a prediction not kept with its instruction, a predicted-taken branch not corrected, a wrong target accepted, recovery to the wrong pc |
| Predictor policy | 9 | tag or valid bit ignored, a prediction that flips after one miss, a counter that never counts down or wraps round, a target that is not refreshed, a second lookup or training port that does not work |
| Prefetching in the cache | 7 | a victim that can still be hit while it is being replaced, a prefetch that evicts the line in use or duplicates one already present, a hit made to wait for a line on its way |
| Prefetcher policy | 7 | a stride table that ignores which instruction it is, a proposal after a single repeat, the distance ignored, a zero stride prefetched, a stride never forgotten |
| Out-of-order core | 29 | a load that passes a store of unknown address or an overlapping one, a result missed at dispatch, a wake-up that ignores the tag, a rename table not cleared or cleared wrongly, structures that survive a flush, a store written before it is known not to trap, retirement before the result is there, a taken branch that continues at pc + 4 |
| Two instructions per cycle | 36 | the second of a pair missing the first one's result, the first of two writers kept in the rename table or the register file, a pair dispatched into one free entry, three results for two buses, both units executing one station, a pair fetched across a line boundary or past a predicted taken branch, the second retiring although the first redirects fetch or although it traps, a store retiring second, two retirements counted as one |
| Cache policies that lose data | 14 | a write-through store not sent to memory, a store going round a line that is on its way, a read overtaking a queued write to its line, a full write buffer accepting a store, a store merged into a write already sent or into another line's, a dirty victim queued without its bytes or under the wrong address, a queued write freed before memory has answered |
| Cache policies that only misbehave | 12 | FIFO order disturbed by a hit, LRU order not updated by one, a hit forgotten when a line arrives in the same set, random replacement that ignores empty ways or never changes its choice, a pseudo-LRU tree that is not updated, points the wrong way or is only half used, a line marked dirty under write-through, a clean victim written back, a write buffer that does not merge, a miss started without room for its victim |
| Between the cache levels | 9 | a line written ignoring its strobes, part of a line passed on as a whole one, the strobes or the address of a request lost in the arbiter, a write passed down as a read, a request forgotten, the answer given to the wrong cache, a request changed while it is being served, the L2's latency ignored |
| Inclusion | 15 | a line evicted without being taken back, the wrong line taken back, a dirty copy from above dropped or left clean or not passed down, a cache never told to give a line up, a line not given up, given up as clean, or with another line's content, an access or a prefetch in the cycle a line is given up, the victim buffer not searched, a store in flight lost track of when its line is taken |
| Victim buffer | 10 | the victim not put into the buffer, the dirty bit lost on the way in or on the way out, a swap that leaves the victim's data behind, a buffer that answers for any address, a dirty line pushed out and not written back, a prefetch or a store that goes round a line the buffer holds, the same entry replaced every time |

All 198 are currently caught. A full run takes about four and a half hours.

What the check has taught, phase by phase:

- Two bugs were at first caught only by random programs: a load-use stall
  ignoring `rs2`, and a reserved `funct7` value not treated as illegal.
  Directed checks were added for both (`hazards.S`, and the
  reserved-encoding checks in `traps.S`).
- A lookup that ignores the valid bit was caught by nothing at first. Tags
  that have never been written are `X` in simulation, and an `X` never
  matches, so the simulator was hiding the bug. The cache testbench now
  fills the tag and data arrays with plausible garbage before reset, as real
  memory cells would hold.
- Evicting the wrong line is invisible to every program, because the data
  stays correct; only the cache testbench's direct check of the LRU order
  sees it.
- One candidate was dropped because it was not a bug: removing the hold
  condition from the pc update changes nothing, since fetch is never ready
  while the pipeline is held. A planted change that cannot alter behaviour
  cannot be caught, and says nothing about the tests.
- Five of the six predictor-policy bugs fail no program at all, directed or
  random, in any configuration. A predictor that forgets its tags, never
  counts down, or flips after one miss still produces correct results, only
  more slowly. They are caught by the predictor testbench alone, which is
  the reason it exists. The same holds for all seven prefetcher-policy bugs
  and the prefetcher testbench.
- One out-of-order bug was caught by nothing at first, and for a reason
  worth remembering: letting a load pass an older store whose address is not
  yet known. `ooo.S` already had checks for exactly that, with the store's
  address made "late" by a chain of additions. But on a core that dispatches
  one instruction per cycle such a chain is never late: it executes as fast
  as the instructions behind it arrive. The checks tested nothing. Only a
  cache miss makes a value late on this core, so checks were added in which
  the store's address or data comes from a load that misses. The earlier
  checks are kept; they become real on a wider core.
- All 36 pair bugs were caught on the first run, each by directed and by
  random programs both, and by `pairs.S` in particular. That is the opposite
  of the out-of-order experience above, and for the reason given there: at
  width 2 a dependent neighbour really is late, so ordinary code exercises
  the pair logic constantly, and the random generator's pool of only eight
  registers makes neighbours depend on each other all the time.
- One pair candidate was dropped because it was not a bug. Making the
  second of a pair wait for the first one's result when it does not read
  that register is a false dependence: the instruction starts later and
  computes the same thing. Its opposite number is a real bug and is kept:
  waiting for a first instruction that writes no register waits for a
  result that never comes, and the run stops making progress.
- One out-of-order bug only matters at width 1: with a single bus, letting
  the ALU fire while a load has the bus loses a result. At width 2 the same
  line is correct, because there is a second bus. The script therefore
  tries both widths.
- The three bugs in the predictor's second ports fail no program, like the
  other predictor-policy bugs. Only the predictor testbench sees them.
- One prefetching bug was caught by nothing at first: a newly installed
  line not being marked most recently used. Results stay correct, and the
  existing replacement-order check happened not to notice, but a prefetched
  line would then be the next one evicted. The cache's replacement rests on
  an invariant (within a set the ages are the numbers 0 to WAYS-1, each
  once), and the cache testbench now checks that invariant directly.

- The twelve policy bugs that only misbehave change no commit trace and no
  byte of memory, in any program, in any configuration. Each is caught by
  the cache model, in program runs and in the cache testbench. Three older
  bugs turned out to be of the same kind and are now seen in program runs
  too, where before only one directed check in the cache testbench saw
  them: evicting the most recently used line, not marking a new line as
  recently used, and counting a prefetched line as useful twice.
- One of the twelve is the Phase 3 cache: planting "a hit forgotten when a
  line arrives in the same set" restores exactly what that cache did. The
  model catches it (with two ways the old behaviour happens to be right, so
  it takes three or more to show). So the flaw was real, and it had passed
  every test for five phases.
- The model does not see everything a trace misses. A hit made to wait for
  a line that is on its way is a pure delay; the event log has an order but
  no clock, and the model accepts it. Only the cache testbench's directed
  timing check catches that one. Decisions are the model's business;
  delays still need a check of their own.
- Two of the new bugs were at first caught by the cache testbench
  alone, and by no program on any core. Both showed that a configuration
  was less adversarial than it looked.
  A store going round a line that is on its way needs a prefetch of exactly
  the line the program is about to store to. The random prefetcher, which
  was in every no-allocate configuration, picks among two thousand lines
  and practically never does. A next-line prefetcher does it constantly,
  provided the stores come close together. A configuration with one was
  added (`p-na-next`), and `wpolicy.S` got two loops with one store per
  line.
  A miss starting without room for its dirty victim needs the write buffer
  to be full at that moment. Behind the one-line cache the buffer had two
  entries, and because one write is sent between any two reads it was never
  full when a miss started. It now has one entry.
- A process error is worth recording. A new test program was added to
  `sw/tests/` while this check was running, before it assembled. A program
  that cannot be assembled fails on every core whatever the RTL does, so
  while the file was there any planted bug would have looked caught. The
  results were thrown away and the whole check was run again. The script
  now stops if a program fails to assemble or fails on the golden model.

- The hierarchy bugs split four ways, and the split says what each check
  is for. Bugs that lose or corrupt data (a dropped strobe, a dirty copy not
  passed down, a victim buffer that forgets a dirty bit) are caught by the
  trace or the memory image, as always. Two change only a decision inside
  one cache, and the model alone catches them: a victim buffer that always
  replaces the same entry, and a store that is sent twice because its line
  was taken while it waited. Seven break only the relation between two
  caches: every result is right, each cache's own event log is in order,
  and the first-level cache simply still holds a line the L2 has evicted.
  Nothing but the inclusion invariant in the RTL sees those. And two change
  nothing but timing or progress, and only the hierarchy testbench sees
  them: the L2's latency ignored (its directed timing check), and a request
  that the arbiter lets change while the L2 is serving it, which every
  program survives and which makes two requests evict each other's line
  for ever when the L2 has one line.
- One hierarchy bug was caught by nothing at first: a write-through L2
  passing part of a line to memory as a whole line. No configuration sent
  it part of a line. A write-back data cache hands the L2 only whole lines,
  and the one configuration without first-level caches had a write-back
  L2, which never passes a store on. `l2-only` now has a write-through L2
  that does not allocate, and the hierarchy testbench has a configuration
  that is write-through at both levels.
- One candidate was dropped because it was not a bug: an arbiter that
  always prefers the data cache. Requests are registered, so the cache it
  prefers needs a cycle to ask again, and in that cycle the other one is
  served. Neither can be passed over twice whatever the preference. The
  fairness check stays in the RTL; it guards the property, not this
  particular way of getting it.
- **The check had a hole of its own.** One planted bug made the final
  memory image unknown (`x`). The regression script could not parse the
  checksum and crashed; the mutation script saw no failing test in the
  output of a script that had died, and reported the bug as caught by no
  directed program. It looked like a gap in the tests and was a gap in the
  tools. An unknown checksum is now an ordinary failure, and the mutation
  script stops if the regression does not finish with its line of totals.
  A tool that checks the tests needs the same suspicion as the tests.

The script edits RTL in place, so it protects against being interrupted: it
keeps a backup of the file it is changing, restores it on the next start if
it finds one, and `python tools/mutation.py --check` confirms that no
planted bug is left in the tree. (This was added after an interrupted run
left one behind.)

## 5. Proving that speculation and timing are invisible

Caches, predictors, prefetchers and out-of-order execution must change how
fast a program runs and nothing else. The regression therefore repeats every
program under varied configurations and requires the identical trace each
time. The configurations in use today (`CONFIGS` in `tools/regress.py`):

| Configuration | Instruction cache | Data cache | Memory latency | Branch predictor | Prefetch (instructions / data) |
| --- | --- | --- | --- | --- | --- |
| `ideal` | none | none | 0 | none | - |
| `ideal-bp` | none | none | 0 | 64 entries, 2-bit | - |
| `ideal-garbage` | none | none | 0 | 16 entries, 2-bit, with garbage | - |
| `nocache-jitter` | none | none | 1 plus random 0-3 | 8 entries, 2-bit, with garbage | - |
| `one-line` | 1 line | 1 line | 1 plus random 0-2 | 4 entries, 2-bit | next line / random |
| `dm` | 32 lines, direct-mapped | 32 lines, direct-mapped | 10 | none | - |
| `pf-stride` | 32 lines, direct-mapped | 16 sets x 2 ways | 10 | 64 entries, 2-bit | next line / stride |
| `4way-tiny` | 2 sets x 4 ways | 2 sets x 4 ways | 2 plus random 0-3 | 8 entries, 1-bit tags | next line / stride, distance 1 |
| `icache-only` | 4 sets x 2 ways | none | 1 plus random 0-2 | 4 entries, 2-bit tags, with garbage | random / - |
| `dcache-only` | none | 4 sets x 2 ways | 1 plus random 0-2 | 1 entry, last-time | - / next line |
| `pf-garbage` | 4 sets x 2 ways | 4 sets x 2 ways | 1 plus random 0-3 | 8 entries, 2-bit, with garbage | random / random |

The next nine vary the cache's policies (ARCHITECTURE.md section 6), again
on small caches with random latency and with prefetches or garbage
predictions behind them:

| Configuration | Caches (instruction / data) | Replacement | A store that hits / misses | Write buffer | Memory latency | Branch predictor | Prefetch (instructions / data) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `p-fifo` | 2 x 4 / 2 x 4 | FIFO | write-back / allocate | none | 1 plus random 0-3 | 8 entries, with garbage | next line / random |
| `p-random` | 4 x 2 / 1 x 4 | random | write-back / allocate | none | 1 plus random 0-3 | 8 entries, 2-bit | next line / stride, distance 1 |
| `p-plru` | 2 x 4 / 2 x 4 | pseudo-LRU | write-back / allocate | none | 2 plus random 0-2 | 16 entries, 2-bit | random / random |
| `p-wt` | 4 x 2 / 4 x 2 | LRU | write-through / allocate | none | 1 plus random 0-2 | 8 entries, with garbage | - / random |
| `p-wt-buf1` | 4 x 2 / 1 x 2 | FIFO | write-through / allocate | 1 entry | 1 plus random 0-3 | 4 entries, 2-bit | next line / random |
| `p-na` | 4 x 2 / 2 x 2 | LRU | write-back / no-allocate | none | 1 plus random 0-2 | 8 entries, with garbage | - / random |
| `p-na-buf` | 2 x 2 / 1 x 4 | pseudo-LRU | write-through / no-allocate | 2 entries, merging | 2 plus random 0-3 | 8 entries, 2-bit | random / random |
| `p-wb-buf` | 4 x 1 / 1 line | LRU | write-back / allocate | 1 entry | 1 plus random 0-3 | 8 entries, with garbage | next line / random |
| `p-na-next` | 4 x 2 / 2 x 2 | LRU | write-back / no-allocate | 2 entries | 2 plus random 0-2 | 8 entries, 2-bit | next line / next line |

The last ten have what Phase 9 added: a second-level cache (L2) below both
caches, inclusive or not, and victim buffers. The L2 is absurdly small in
all of them, which is the point: a line is evicted from it, and under
inclusion taken back from the caches above, almost every time it is asked
for something.

| Configuration | Caches (instruction / data) | Victim buffers | L2 | L2 latency | Policies | Memory latency | Branch predictor | Prefetch (instructions / data) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `l2-one` | 4 x 2 / 4 x 2 | - | 1 line | 1 plus random 0-2 | all default | 1 plus random 0-2 | 8 entries, with garbage | next line / random |
| `l2-incl` | 4 x 2 / 2 x 4 | - | 2 x 2, inclusive | 1 plus random 0-3 | all default | 1 plus random 0-3 | 8 entries, 2-bit | random / random |
| `l2-wt-wb` | 4 x 2 / 4 x 2 | - | 4 x 2 | 1 | data cache write-through with a 1-entry buffer; L2 FIFO, write-back | 2 plus random 0-2 | 8 entries, with garbage | - / next line |
| `l2-wb-wt` | 2 x 2 / 2 x 2 | - | 2 x 4 | 2 plus random 0-1 | data cache write-back; L2 write-through, no-allocate, 2-entry merging buffer | 1 plus random 0-3 | 8 entries, 2-bit | random / random |
| `l2-incl-wt` | 4 x 2 / 4 x 2 | - | 1 x 4, inclusive | 1 plus random 0-1 | data cache write-through, no buffer; L2 pseudo-LRU with a 1-entry buffer | 1 plus random 0-2 | 8 entries, with garbage | next line / random |
| `l2-only` | none / none | - | 2 x 2 | 1 plus random 0-2 | L2 random replacement, write-through, no-allocate, 1-entry buffer | 1 plus random 0-2 | 8 entries, 2-bit | - |
| `vb-one` | 1 line / 1 line | 2 / 2 entries | none | - | all default | 1 plus random 0-3 | 8 entries, with garbage | next line / random |
| `vb-na` | 4 x 1 / 2 x 2 | 1 / 3 entries | none | - | data cache random replacement, no-allocate, 1-entry buffer | 2 plus random 0-2 | 8 entries, 2-bit | - / random |
| `vb-wt` | 2 x 2 / 2 x 2 | 3 / 1 entries | none | - | FIFO; data cache write-through, 2-entry merging buffer | 1 plus random 0-2 | 8 entries, with garbage | next line / next line |
| `vb-incl` | 2 x 2 / 2 x 2 | 2 / 3 entries | 2 x 2, inclusive | 1 plus random 0-2 | data cache with a 1-entry buffer | 1 plus random 0-2 | 8 entries, 2-bit | next line / random |

All three cores run in all thirty. The out-of-order core runs in more,
which vary the size of its window:

| Configuration | Width | Reorder buffer | Reservation stations | Load/store queue | Rest of the machine |
| --- | --- | ---: | ---: | ---: | --- |
| `ooo-32` | 1 | 32 | 16 | 16 | as `pf-stride` |
| `ss` | 2 | 32 | 16 | 16 | as `pf-stride` |
| `ooo-tiny` | 1 and 2 | 2 | 1 | 2 | ideal memory, 2-bit predictor |
| `ooo-small` | 1 and 2 | 4 | 2 | 2 | as `pf-garbage` |
| `ss-pair` | 2 | 2 | 2 | 2 | ideal memory, 16-entry predictor with garbage |

Every directed program runs in every configuration of each core, and the
kernels as section 1 describes (all of them on the measured machines, a
tenth of them elsewhere, all of them everywhere with `--full`).
Each random program runs in one per core, chosen so that plain and
fault-injecting programs both reach every configuration.

The awkward settings matter most:

- A one-line cache evicts on almost every access, so every dirty line is
  written back and fetched again many times.
- Random latency moves every miss relative to every branch, trap and stall,
  so an interaction that only goes wrong at one alignment gets hit.
- "With garbage" replaces a quarter of all predictions with nonsense:
  "taken" for an instruction that is not a branch, to the next instruction,
  the previous one, the same one, a random address, a misaligned one, or one
  outside memory. No real predictor behaves like this. It is there because a
  good predictor is right so often that recovery is rarely exercised, and
  never for an ordinary instruction; with garbage, recovery runs constantly
  and from every kind of instruction, including ones next to traps and
  cache misses. The trace must not change.
- Short tags make different branches share an entry and inherit each
  other's predictions.
- A random prefetcher asks for arbitrary lines at arbitrary moments. Lines
  are evicted and replaced behind the processor's back, including dirty ones
  and the neighbours of the line in use. `pf-garbage` combines this on both
  caches with garbage predictions and random memory latency: nothing about
  the timing or the speculation in that configuration is sensible, and the
  results must still be exact.
- A one-entry write buffer is full after every store, and a one-line data
  cache behind a write buffer (`p-wb-buf`) evicts a dirty line on almost
  every miss, often the very line that is wanted next: the read then has to
  wait for the queued write to that line, the rule the buffer's correctness
  rests on.
- Random prefetches behind write-through or no-allocate bring lines in and
  throw them out while stores to those same lines are on their way to
  memory.
- A random prefetcher almost never asks for the line the program is about
  to store to; a next-line prefetcher does it all the time. `p-na-next` is
  there for that one meeting: a store that finds its line absent but on its
  way must wait for it, not go round it.
- An inclusive L2 of four lines under first-level caches of eight
  (`l2-incl`) cannot hold what they hold. Almost every line it brings in
  costs the caches above a line, often a dirty one, and often the one in
  use. A random hit latency moves each of those take-backs against every
  access.
- `l2-incl-wt` is there for one meeting: a write-through store that is
  already waiting for the L2's answer when the L2 takes its line back. The
  store has to finish all the same, once, without its line.
- With no first-level caches (`l2-only`) every load and store reaches the
  L2 as a line transfer with byte strobes, and that L2 is write-through and
  does not allocate: every store of every size goes through it to memory as
  a partial write. (Under a write-back data cache the L2 only ever sees
  whole lines, and a bug in how it passes on part of a line shows nowhere.
  The mutation check found that hole.)
- A victim buffer beside a one-line cache (`vb-one`) is used on almost
  every miss, and under no-allocate (`vb-na`) a store can find its line in
  the buffer, where it must not go round it.
- In `vb-incl` the first-level caches and their victim buffers together
  hold more lines than the inclusive L2 below them, so lines are taken back
  out of the victim buffers too.
- The smallest window (`ooo-tiny`) keeps the reorder buffer, the single
  reservation station and the load/store queue full almost all the time, so
  every "cannot dispatch yet" condition is exercised constantly. `ooo-small`
  adds everything else that is adversarial on top of a 4-entry buffer.
- At width 2 the small windows decide, cycle by cycle, whether a pair goes
  in together or is split. With one reservation station (`ooo-tiny`) two
  ALU instructions can never be dispatched together; with a 2-entry buffer
  that has room for exactly one pair (`ss-pair`) the buffer fills and
  empties two at a time, under garbage predictions.

### What a trace cannot see: the cache model

A cache must never change a result, and the checks above prove that it does
not. That is also their blind spot. A cache that evicts the most recently
used line instead of the least, that writes back lines nobody changed, or
whose pseudo-LRU tree points the wrong way, passes every one of them: the
data is right, and only the speed is wrong. Before Phase 8 the one policy
the cache had (LRU) was checked by a single directed test in the cache
testbench. With four replacement policies, four write policies and a write
buffer, that does not scale.

`tools/cachesim.py` is a second reference, for behaviour instead of results.
It knows nothing about time or data: only which lines are in the cache, in
which way, which are dirty, what is on its way to memory, and what each
policy must do next. In simulation each cache writes an event log, one line
per event:

```text
F r 4460 6e60 c      a line is brought in for a read; it replaces line 6e60, which is clean
R 4460               the line is requested from memory
I 4460               ... and installed
H w 4468 3           a store of 8 bytes completes on a line in the cache
N 51c0 2             a store completes without its line (no-allocate)
w 7e60               a queued write is sent to memory
M 7e60 ffffffff      ... and has arrived (one strobe bit per byte)
S r 4040 5040 d      a line comes out of the victim buffer and changes places with line 5040, which is dirty
X 4460 d             the line is given up to the inclusive cache below, dirty
B 4460 d             (in the log of that cache) the line is taken back; a dirty copy came down with it
```

With a victim buffer the `F` line has two more fields, the line pushed out
of the buffer and whether it was dirty. A second-level cache is asked for
whole lines, so its `H` and `N` lines name the line and carry the byte
strobes of the write in place of a size.

The model replays the log. What the cache was asked is taken from the log;
what the cache decided is checked:

- a line is brought in only when it is absent, and never for a store when
  stores do not allocate;
- the victim is the one the policy chooses, and it is dirty exactly when the
  model says so;
- with a victim buffer, the victim goes into the entry the model expects,
  the line pushed out is the one the model expects, and a line comes out of
  the buffer only if it is there;
- an inclusive cache evicts only the line it has taken back in the event
  just before, and a line that came down dirty leaves dirty;
- a line that is given up was present, and was dirty exactly when the log
  says so;
- an access completes only on a line that is present;
- a line is read from memory only when no write to it is still on its way;
- the writes that reach memory are exactly the expected ones, with the
  expected bytes, in order, and merged exactly when merging says so;
- the counters the cache reports equal what the log amounts to.

Because the log has an order but no clock, the model needs none either, and
the same model serves every memory latency. The one policy that looks as if
it depended on time, random replacement, was built not to: its shift
register steps once per line brought in.

The comparison is made for every cache of every run in the `rtl` and `bench`
stages (up to three: the two first-level caches and the L2, each against
its own instance of the model) and for every run of the cache and hierarchy
testbenches. The model has its own unit tests (`tools/tests/test_cachesim.py`):
sequences worked out by hand for each policy and for the victim buffer, the
two figures DDCA lecture 22 gives (a 4-way set asked for five lines in turn
never hits under LRU; random replacement does much better), and logs
written by hand that contain each kind of mistake.

Each model sees one cache. What holds between two caches is checked in the
RTL itself (invariants, below).

Its first finds were in this project's own cache:

- **Within a set, a hit and the arrival of a new line in the same cycle.**
  Hits are served while a line is on its way, so the two can coincide. The
  cache updated the ages for both from the same starting values, and the
  update for the new line overwrote the one for the hit. With two ways the
  result happens to be right. With three or more the line that was just hit
  could be left looking old. This had been there since Phase 3; every trace
  was correct and the ages were still a permutation, so nothing noticed. No
  benchmark configuration has more than two ways, so no published number
  changed when it was fixed.
- **An unknown value in the replacement state.** The first version of the
  rewritten cache compared the set of an arriving line with the set of the
  processor's current request, even when there was no request. In simulation
  an idle core's address is unknown, the comparison was unknown, and the
  ages of the set became unknown. From then on the cache always evicted way
  0. Every program still passed. The model reported the first wrong victim
  in the first run.

### The cache model on its own

Replaying a log checks what the cache decided, given what it was asked. It
does not check the model as a simulator: `Cache.access`, which performs one
whole access from start to finish, and which the sweeps
([SWEEPS.md](SWEEPS.md)) use to count misses for hundreds of configurations
that no RTL run covers. The `sweep` stage closes that gap. In each of its
903 RTL runs (43 machines, 21 kernels) the model is also run by itself:

- **Data cache.** The kernel's loads and stores are recorded from the golden
  model, which knows nothing about caches, and replayed through the model in
  the machine's configuration. Seven counters must then equal the RTL's:
  accesses, misses, evictions, write-backs, lines read from memory, writes
  sent to memory, and stores that missed. This is done in the 777 runs
  without an inclusive second level; an inclusive L2 takes lines out of the
  data cache for reasons a model of one cache cannot know.
- **Instruction cache.** The golden model cannot supply the fetches, because
  the RTL also fetches down mispredicted paths. Here the model replays what
  the RTL's own log says the cache was asked (each line it started to bring
  in, each access it completed, each line an inclusive L2 took back) and
  must end with the RTL's misses, evictions, lines read from memory and
  lines taken back. All 903 runs.

The grid has data caches from 256 bytes to 4 KiB in one, two and four ways,
lines of 8 to 128 bytes (every earlier configuration has 32-byte lines), all
four replacement policies, all four write policies, and second-level caches
from 1 to 32 KiB. Every counter agrees in every run.

This is evidence of a different kind from the log replay. There the RTL
supplies the requests and the model checks the answers one by one. Here the
model is given only the program's accesses and has to arrive at the same
totals by itself, including under random replacement, whose every choice
depends on how many lines have been brought in before it. Two independent
implementations of each policy, one in Python and one in SystemVerilog, are
being counted against each other.

The sweeps also use two faster simulators (LRU in any shape, and the optimal
policy). They have unit tests instead (`tools/tests/test_sweep.py`): the fast LRU
equals the reference model on random traces; the optimal policy never misses
more than any real one, and equals an exhaustive search over every possible
choice of victim on small traces; a larger fully associative cache never
misses more than a smaller one; the three classes of miss add up to the
misses.

The first complete run of this comparison failed, and the fault was in the
comparison. The instruction-side replay did not know about lines taken back
by an inclusive L2, and counted 5 misses where the RTL had 293. The log
replay of the same runs had passed, as it should have: the RTL was right.
Not for the first time in this project, the thing doing the checking was the
thing that was wrong.

**Width 1 did not change.** The two widths are one RTL, so making the core
wide could have disturbed the scalar core. It did not: at width 1 every
kernel takes exactly the number of cycles it took before the change
(RESULTS.md, columns `ooo` and `ooo-32`), and the regression keeps running
that width in full.

**Invariants.** The out-of-order core checks, in every cycle of every
simulation, conditions that must hold whatever the program does: `x0` is
never renamed; the reorder buffer's pointers and count agree; the load/store
queue's count matches its entries; no result arrives for an entry that is
already done; nothing retires and traps in the same cycle; a memory
instruction that retires has a queue entry; the two buses never carry a
result for the same entry; a younger instruction never moves ahead of an
older one (in fetch, dispatch or retirement); a system instruction is alone
in the reorder buffer; and at width 1 the second slot is never used. A
violation stops the run with
an error that the regression reports, so a broken invariant cannot hide
behind a result that happens to be right. Icarus has limited support for
SystemVerilog assertions, so these are plain clocked `if` statements.

The memory system has two of its own.

- **Inclusion.** Under an inclusive L2, every line in a first-level cache or
  its victim buffer is in the L2. The property can only break at two
  moments, and both are checked in every cycle: a first-level cache installs
  a line (the L2 must hold it), and the L2 evicts a line (no first-level
  cache may hold it). The testbenches check the whole property once more
  when a run ends. Nothing else can see a broken inclusion: the first-level
  cache that kept the line still has correct data, and each cache's own
  event log is in order.
- **Fairness.** The arbiter never starts serving one cache twice while the
  other one's request waits.

## 6. Performance tracking

Every core exposes counters to the testbench: cycles, instructions retired,
branches and mispredictions, cache accesses and misses, prefetches issued and
useful, flushes. The regression prints them per program, and
`python tools/regress.py bench` runs the benchmark kernels in `sw/bench/` on
every configuration and regenerates `docs/RESULTS.md`, so each feature's
benefit is a number. HISTORY.md section 5 lists the kernels and what each is
meant to show.

`python tools/regress.py sweep` does the same for the cache's parameters
across their range and writes [SWEEPS.md](SWEEPS.md). The charts of the
results are drawn by `python tools/figures.py` from numbers kept in
`docs/data/`, so that they can be redrawn, and checked by the unit tests,
without simulating.

## 7. Known limits

- **The reference is home-grown.** With only Icarus and Python available,
  the golden model and the assembler were both written for this project and
  have not been compared against Spike or the official `riscv-tests`. Three
  things reduce the risk: assembler encodings are unit-tested against values
  worked out by hand (and were compared during development with the course
  project's own encoder, over 400 instructions); the test programs carry independently computed expected
  values; and the RTL is a third implementation, so a bug must be made the
  same way in the model and in the RTL to go unseen. A misreading of the
  specification shared by all of them would still pass. If the tool
  restriction is ever relaxed, running `riscv-tests` under WSL is the single
  most valuable addition.
- **No C programs**, so benchmarks are hand-written kernels and performance
  numbers are indicative rather than comparable with published figures.
- **Simulation only.** Nothing is synthesised; timing and area are not
  evaluated.
- **Trace comparison is only as good as the programs run.** Coverage is
  tracked informally by the instruction and exception lists above.
- **The cache model has no clock.** It checks what each cache decides, not
  when. A cache that makes the right decisions a cycle late passes it. The
  timings that the results depend on are checked by directed tests in the
  block testbenches (a hit served while another line is on its way; the
  cost of a miss at each level), and nowhere else.

## 8. Debugging a mismatch

1. The `rtl` stage reports the configuration, the first differing line, and
   the two trace files: `build/golden/<folder>/<program>.trace` (golden) and
   `build/rtl/<configuration>/<folder>/<program>.trace` (RTL).
2. Open the `.lst` file next to the golden trace to see which source line
   that pc is.
3. Check whether the program fails in `ideal` as well (`-c ideal -k <name>`).
   If it does, the bug is in the pipeline itself; if it only fails with
   caches or latency, it is in the memory system or in how the pipeline
   stalls.
4. The lines before the divergence are known-good, so the bug is in how the
   RTL handled this instruction or something speculative around it.
5. Re-run that program alone with `+vcd=` (section 1) and inspect from a few
   cycles before the commit of the differing instruction.
6. "Final memory differs" names the first byte that is wrong and the dump
   file. Search the golden trace for the last store to that address: the
   store committed correctly (the trace matched), so the data was lost
   afterwards, between the cache, its write buffer and memory.
7. A cache-log failure names the log (`.dc.log`, `.ic.log` or `.l2.log`
   next to the RTL trace), the line and what the model expected. `python
   tools/cachesim.py <log>` with the configuration's parameters replays it
   by hand. The log is in order of events, so the lines just before the
   reported one show what the cache had been asked.
8. `ERROR inclusion` or `ERROR line_arbiter` is an invariant of the memory
   system, reported with the line concerned. Run the program again with
   `+icache_log=`, `+dcache_log=` and `+l2cache_log=` and look for that line
   in all three: the last `F`, `I`, `X` and `B` events for it say which
   cache gained or kept it when it should not have.
9. For a random program, the source is `build/rand/seed_<n>.S`; shrink it by
   removing instructions while the mismatch persists.
