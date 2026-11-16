# Architecture

How the processor is built. [HISTORY.md](HISTORY.md) says how it got here
and what was planned beyond it;
[VERIFICATION.md](VERIFICATION.md) says how it is checked.

There are two cores. Sections 2 to 5 and 7 describe the in-order pipeline;
section 10 describes the out-of-order core, and section 11 what changes when
that core handles two instructions per cycle. The memory system, branch
predictor and prefetchers (sections 6, 8 and 9) are shared by both.

![The whole machine](img/system.svg)

The diagrams, from the whole machine down:

| Level | Diagram | Section |
| --- | --- | --- |
| The machine | [The whole machine](img/system.svg) | above |
| A core | [The in-order core](img/core_inorder.svg) | 2 |
| | [The out-of-order core](img/core_ooo.svg) | 10 |
| The memory system | [The memory hierarchy](img/memory.svg) | 6 |
| Inside a block | [One access to the cache](img/cache.svg) | 6 |
| | [The branch predictor](img/predictor.svg) | 8 |
| | [The life of an instruction](img/ooo_life.svg) | 10 |
| | [The second slot of a two-wide core](img/ooo_pair.svg) | 11 |
| How it is checked | [How every run is checked](img/verification.svg) | [VERIFICATION.md](VERIFICATION.md) |

## 1. Source map

| File | Contents |
| --- | --- |
| `rtl/common/rv_pkg.sv` | Opcodes, exception causes, CSR addresses, the `ctrl_t` control bundle |
| `rtl/common/decoder.sv` | Instruction word to control bundle and immediate; all legality checks |
| `rtl/common/alu.sv` | 64-bit and 32-bit ("W") arithmetic, logic and shifts |
| `rtl/common/regfile.sv` | 32 x 64-bit registers with write-to-read bypass (in-order core) |
| `rtl/common/csr_file.sv` | Machine-mode CSRs, trap entry and return |
| `rtl/common/cache.sv` | Set-associative cache: four replacement policies, write-back or write-through, optional write buffer and victim buffer; also the second-level cache |
| `rtl/common/line_arbiter.sv` | Lets the two first-level caches share the second-level cache |
| `rtl/common/mem_subsystem.sv` | Instruction cache, data cache, the optional second-level cache below them, and the memory map |
| `rtl/common/bpred.sv` | Branch target buffer with 2-bit counters |
| `rtl/common/prefetcher.sv` | Next-line and stride prefetchers |
| `rtl/inorder/inorder_core.sv` | The in-order 5-stage pipeline |
| `rtl/ooo/ooo_core.sv` | The out-of-order core, one or two instructions wide (`WIDTH`) |
| `rtl/ooo/exec_unit.sv` | Its execution unit: ALU plus branch and jump resolution |
| `tb/sim_memory.sv` | Main memory model: 64 KiB RAM, line transfers with latency |
| `tb/core_tb.sv` | Connects core, memory subsystem and memory; writes the commit trace; console and halt |
| `tb/cache_tb.sv` | Unit testbench for the cache |
| `tb/hier_tb.sv` | Unit testbench for the hierarchy: both first-level caches, the arbiter and the second-level cache |
| `tools/cachesim.py` | Model of the cache's policies; replays the event log a cache writes in simulation |
| `tb/bpred_tb.sv` | Unit testbench for the branch predictor |
| `tb/prefetcher_tb.sv` | Unit testbench for the prefetcher |
| `tools/sweep.py` | Design-space sweeps of the cache: fast simulators, the optimal replacement policy, miss classification, and the document and charts of [SWEEPS.md](SWEEPS.md) |
| `tools/svgplot.py`, `tools/figures.py` | The charts and diagrams in `docs/img/`, written as SVG by plain Python |

Everything in `rtl/common/` is used by both cores. The two cores have the
same ports, so the testbench and the memory subsystem do not know which one
they are connected to. The out-of-order core has two additions for width 2:
an input for the second instruction of a fetch and a second commit report
(section 11).

## 2. The in-order pipeline

![The in-order core](img/core_inorder.svg)

```text
        +------+    +------+    +------+    +------+    +------+
 pc --> |  IF  | -> |  ID  | -> |  EX  | -> | MEM  | -> |  WB  | --> commit report
        +------+    +------+    +------+    +------+    +------+
        fetch       decode      ALU         data memory  register
                    reg read    branch      CSRs         write
                    hazards     resolve     trap, mret
            ^           ^          |            |
            |           |          | redirect   | flush
            +-----------+----------+            | (trap / mret)
            +-----------+-----------------------+
```

| Stage | Does | Architectural state it can change |
| --- | --- | --- |
| IF | Reads the instruction at `pc`; next `pc` is `pc + 4` unless redirected | none |
| ID | Decodes, reads two registers, detects the load-use hazard | none |
| EX | Selects forwarded operands, runs the ALU, decides branches and jumps | none |
| MEM | Loads and stores; CSR read and write; takes traps and `mret` | memory, CSRs |
| WB | Writes the destination register | register file |

Only MEM and WB change architectural state, and they are the last two
stages. That is the property the exception design rests on (section 4).

### Control bundle

The decoder reduces an instruction to a packed struct, `ctrl_t`, that
travels with it: which operands it reads, the ALU operation, whether it is a
branch, jump, load, store, CSR access, `mret`, `ecall` or `ebreak`, and
whether it writes a register. An all-zero bundle is a bubble. An illegal
instruction decodes to a bundle with only `illegal` set, so it can do
nothing but raise its exception.

## 3. Hazards

| Hazard | Case | Handling | Cost |
| --- | --- | --- | --- |
| Data | Producer one instruction ahead | Forward the ALU result from MEM into EX | 0 |
| Data | Producer two ahead | Forward the writeback value from WB into EX | 0 |
| Data | Producer three ahead | Register file bypasses the write to the read port | 0 |
| Data | Producer is a load or CSR read, consumer right behind | Consumer waits one cycle in ID, then takes the WB forward | 1 cycle |
| Control | Branch or jump, predicted correctly | Fetch already followed it | 0 |
| Control | Any instruction after which fetch went the wrong way | Found in EX; the two younger instructions are squashed | 2 cycles |
| Control | Trap or `mret` | Taken in MEM; the three younger instructions are squashed | 3 cycles |
| Memory | Instruction not available (instruction cache miss) | IF waits and feeds bubbles into ID; later stages keep going | the miss |
| Memory | Data access not finished (data cache miss) | Every stage holds still until it completes | the miss |

When both forwarding sources match, MEM wins because it holds the younger
value. Register `x0` is never forwarded. Where fetch goes after each
instruction is decided by the branch predictor (section 8); with the
predictor off it always goes to the next instruction, so every taken branch
and every jump pays the two cycles.

## 4. Precise exceptions

**Definition** (DDCA lecture 13): when an exception is handled, every
instruction before the faulting one has completed, and neither the faulting
instruction nor any after it has changed architectural state.

**Rule used here:** an exception is not acted on where it is discovered. The
instruction is marked, carries its cause and `mtval` value down the
pipeline, and traps when it reaches MEM.

Why MEM: it is the first stage that changes anything other than the register
file, and by the time an instruction is in MEM every older instruction is in
WB or gone. So in the cycle the trap is taken:

- the older instruction in WB writes its register as normal;
- the trapping instruction is prevented from touching memory or CSRs, and
  goes on to WB only to be reported, with its register write cancelled;
- the three younger instructions are in IF, ID and EX, where nothing has
  been changed yet, and are squashed;
- `mepc`, `mcause` and `mtval` are written, the interrupt-enable bit is
  stacked, and fetch restarts at `mtvec`.

`mret` and the CSR instructions execute in the same stage, so CSR reads, CSR
writes, traps and returns all happen in program order without any extra
interlock.

| Cause | Exception | Found in | `mtval` |
| --- | --- | --- | --- |
| 1 | Instruction access fault (fetch outside RAM) | IF | the pc |
| 2 | Illegal instruction, including bad CSR accesses | ID | the instruction word |
| 11 | `ecall` | ID | 0 |
| 3 | `ebreak` | ID | the pc |
| 0 | Jump or taken branch to an address that is not a multiple of 4 | EX | the target |
| 4 / 6 | Misaligned load / store | MEM | the address |
| 5 / 7 | Load / store outside RAM | MEM | the address |

An instruction found faulty in an early stage and one found faulty in a late
stage can be in the pipeline together, the younger one discovered first.
Because both wait for MEM, the older one always traps first and the younger
one is squashed with the rest. `sw/tests/precise.S` checks exactly this.

A jump to a misaligned target is an exception on the jump itself: it does
not redirect fetch and does not write its link register.

**Timeline** of `ld` faulting at address `A`, followed by a store `S`:

```text
cycle      n      n+1     n+2     n+3     n+4
ld         EX     MEM*    WB(T)
S          ID     EX      -       (squashed in n+1)
next       IF     ID      -       (squashed)
handler                   IF      ID      EX
```

At `MEM*` the fault is seen, `S` is still in EX and has written nothing, and
the pc is redirected. `WB(T)` reports the trap in the commit trace.

## 5. Control and status registers

Machine mode only. `mstatus` implements `MIE` and `MPIE`; `MPP` reads as 3.
`mtvec` is direct mode. `mepc` and `mtvec` keep their low two bits at zero.
`minstret` counts retired instructions (a trap is not a retirement).
`mcycle` counts clock cycles and is therefore the one register a program
under trace comparison must not read. The full list and the reset values are
in VERIFICATION.md section 2.

The decoder decides legality from the instruction bits alone: an
unimplemented CSR address, or a write to a read-only one, is an illegal
instruction before it reaches the CSR file. A set or clear with a zero source
does not count as a write.

## 6. Memory system

```text
            +----------------- mem_subsystem ------------------+
 core IF -->|  memory map  -->  instruction cache  ------------|--> memory port A
            |                                                  |    (line transfers,
 core MEM ->|  memory map  -->  data cache  -------------------|--> memory port B  latency)
            +--------------------------------------------------+
```

That is the machine of the first eight phases, and still the default. With
`L2_ENABLE = 1` a second-level cache sits below both caches and is the only
thing that talks to memory ("The second level", below):

![The memory hierarchy](img/memory.svg)

```text
            +----------------------- mem_subsystem -----------------------+
 core IF -->|  memory map --> instruction cache --+                       |
            |                                     +--> arbiter --> L2 ----|--> memory port B
 core MEM ->|  memory map --> data cache ---------+                       |
            +-------------------------------------------------------------+
```

### Core interface

The instruction port also returns the instruction that follows the one asked
for, when it lies in the same cache line; only the two-wide core uses it
(section 11).

Each port has a `ready` signal. The core gives the data port an address, a
size (1, 2, 4 or 8 bytes) and right-justified data; the memory side places
the bytes and the core does the sign or zero extension of loaded values.
Alignment is checked by the core, and a misaligned access is never sent.
Each port also returns a fault flag when nothing is mapped at the address.

A cache hit answers in the same cycle, so with warm caches the pipeline
runs exactly as it did on ideal memory. A miss simply leaves `ready` low:

- **Fetch not ready.** IF keeps asking and puts bubbles into ID. Everything
  already in the pipeline carries on. If a branch or trap redirects fetch
  meanwhile, the pc moves on and the instruction being waited for is
  dropped; the cache finishes fetching that line anyway and keeps it.
- **Data access not ready.** The whole pipeline holds: every stage register,
  the pc and the CSRs keep their values until the access completes. Holding
  every stage, not just the ones behind MEM, leaves the forwarding sources
  in place, so an operand cannot go stale while its producer drains away.
  The instruction sitting in WB during a hold has already been reported, so
  the commit report is suppressed for it until the pipeline moves again.

A faulting access is answered at once, so a trap never has to wait for a
miss.

### Memory map

`mem_subsystem` owns the memory map. An address outside the 64 KiB of RAM is
answered immediately with a fault and never reaches a cache. A store to one
of the two memory-mapped registers is accepted and dropped; the testbench
prints or halts when that store is reported at commit, so console output and
the end of the run follow program order.

### The cache

One module, `cache.sv`, serves as instruction cache, data cache and
second-level cache.

![Inside the cache](img/cache.svg)

| Property | Choice |
| --- | --- |
| Organisation | `SETS` x `WAYS` lines; `WAYS = 1` is direct-mapped |
| Line size | 32 bytes (4 doublewords, 8 instructions) |
| Address split | tag, set index, byte offset |
| Hit | Combinational: data and `ready` in the same cycle |
| Replacement | A parameter: LRU, FIFO, random or pseudo-LRU (below) |
| Stores | Two parameters: what a store does when it hits and when it misses (below) |
| Bringing a line in | Take the victim out of the cache (keeping a copy if it is dirty), write the copy back, fetch the line, install it as most recently used |
| During that | Hits on every other line are still served |
| Miss cost | memory latency + 2 cycles, plus latency + 1 if a dirty line has to be written back and there is no write buffer |
| Prefetch | A port for requests that the processor is not waiting for (section 9) |

Instruction and data caches are separate because they are used in different
pipeline stages in the same cycle (DDCA lecture 22 gives the same reason for
split first-level caches).

The defaults (LRU, write-back, allocate, no write buffer) are the cache of
Phase 3, and with them every benchmark takes exactly the cycles it took
before the other choices existed.

### Replacement policies

`REPL` selects which line of a full set makes room for a new one. Whatever
the policy, an empty way is used first, the lowest one if there are several
(lecture 22 puts it the same way: evict an invalid block if there is one).
Ways are empty after reset, and again whenever an inclusive second-level
cache has taken a line back ("Inclusion", below).

| `REPL` | Policy | State | Victim |
| --- | --- | --- | --- |
| 0 | LRU | An age per line; within a set the ages are always the numbers 0 to `WAYS`-1, each once. A hit or an install makes the line age 0 and every line that was younger one older. | The line with age `WAYS`-1 |
| 1 | FIFO | The same ages, changed only when a line is installed. | The line that was installed longest ago, however recently it was used |
| 2 | Random | One 16-bit shift register for the whole cache. | Its value modulo `WAYS` |
| 3 | Pseudo-LRU | A binary tree of `WAYS`-1 bits per set (`WAYS` a power of two). Every use of a line turns the bits on its path to point at the other half. | Where the bits point, from the root down |

Three details are worth knowing.

- The shift register steps once per line brought in, not once per cycle.
  The choice therefore depends only on what the cache was asked, not on how
  long memory took, and a model without any notion of time can reproduce it.
- Pseudo-LRU is the usual cheap stand-in for LRU: `WAYS`-1 bits per set
  instead of an ordering of all the ways. It protects the line used last
  but can evict the second most recently used one. For two ways it is LRU
  exactly. The lecture names other approximations (not-MRU, hierarchical
  LRU) and notes that most processors do not implement true LRU.
- A hit and the arrival of a new line can fall into the same set in the
  same cycle, because hits are served while a line is on its way. The new
  line then counts as the more recent of the two. The Phase 3 cache got this
  wrong for three or more ways: the hit was forgotten. No program could
  tell; the cache model did (VERIFICATION.md section 5).

### Stores

Two independent choices, the ones lecture 22 lists.

| Parameter | 0 | 1 |
| --- | --- | --- |
| `WRITE_HIT` | **Write-back.** The store changes the line and sets its dirty bit. Memory sees the line when it is evicted. | **Write-through.** The store changes the line and is also sent to memory. No line is ever dirty and an eviction never writes. |
| `WRITE_MISS` | **Allocate.** The line is brought in and then written, like a read miss. | **No-allocate.** The store is sent to memory and the line is not brought in. |

All four combinations work. One case needs care: under no-allocate, a store
to a line that is absent but already on its way (a prefetch, or a read that
was abandoned) must not go round it, or the line would arrive without the
store's bytes. Such a store waits for the line and is then an ordinary hit.

A store that is sent to memory carries one strobe per byte, so only its own
bytes are written.

### The write buffer

Without one (`WBUF_ENTRIES = 0`) the processor waits for memory whenever a
write has to go there: a store under write-through or no-allocate waits for
its own write, and a miss waits for the write-back of its dirty victim
before the line it wants is even requested.

With one, those writes go into a first-in first-out queue instead. The
store is finished, or the miss moves on to its read, in the same cycle; the
queue is sent to memory when the memory port is free. A store waits only
when the queue is full.

```text
 stores (write-through, no-allocate) --+
                                       +--> [ oldest | ... | newest ] --> memory
 dirty victims ------------------------+           ^
                                                   | a read of a line waits while
 reads of new lines -------------------------------+ a write to it is in here
```

- **A read never overtakes a write to the same line.** Memory is the only
  place the read looks; nothing is forwarded out of the queue. If a write to
  the wanted line is queued or on its way, the read waits until memory has
  it.
- **Otherwise reads go first**, since the processor is usually waiting for
  them. One queued write is sent between any two reads, so the queue always
  makes progress.
- **Merging** (`WBUF_MERGE`). A store to a line that already has a queued
  write is added to that entry instead of taking a new one: its bytes
  replace or extend the entry's. It joins the newest such entry, and never
  one that has already been sent to memory. Four stores that fill a line
  then cost one write instead of four.
- A queue entry stays in the queue until memory has answered, so that the
  first rule covers a write that is in flight.

The lectures do not describe write buffers; the
design here is the simplest one that keeps the two rules above.

### The victim buffer

`VICTIM_ENTRIES` puts a few lines beside a cache that are found by their
full address, not by a set index. It is Jouppi's victim cache (ISCA 1990),
which lecture 21 shows beside a direct-mapped cache with the remark that it
"can avoid ping ponging of cache blocks mapped to the same set".

```text
                  evicted line (with its dirty bit)
   cache array  ------------------------------------>  victim buffer
   SETS x WAYS  <------------------------------------  a few entries
                  wanted line, if the buffer has it         |
                                                             v
                                       the line pushed out leaves the cache
                                       (written back if it is dirty)
```

- **A line the cache evicts goes into the buffer** instead of leaving, with
  its dirty bit and without being written anywhere. It takes an empty entry
  or, when all are in use, the entry a round-robin pointer names. Only the
  line pushed out of that entry leaves the cache, and only that one is
  written back if it is dirty.
- **A miss on a line that the buffer holds** costs one cycle and no traffic:
  the line and the cache's victim change places, and the access is a hit in
  the next cycle. The dirty bit travels with the line both ways.
- **A line is in the array or in the buffer, never in both.** So a line the
  buffer holds counts as present for everything that could otherwise create
  a second copy: a prefetch request for it is dropped, and a store to it
  swaps it in even when stores do not allocate.
- The buffer is searched in the same cycle as the array, so a miss that it
  cannot serve goes on to the next level without delay. (The lecture's
  warning, "increases miss latency if accessed serially with L2", is about
  designs that look in the buffer first and in the L2 afterwards.)

### The second level

The second-level cache (L2) is the same module again. Three things make
that possible.

| Piece | What it does |
| --- | --- |
| Line port (`LINE_PORT = 1`) | The upper side of the cache transfers whole lines: a read returns the line, a write carries a line and one strobe per byte. That is exactly what the memory side of the cache above produces, so the L2 is asked for lines and written with strobes, and inside it nothing else changes. |
| Hit latency (`HIT_LATENCY`) | The cache looks at a request only after it has been present for that many cycles. A first-level cache has 0. `HIT_JITTER` adds a random number of cycles, for verification. |
| Arbiter (`line_arbiter.sv`) | Both first-level caches send their requests to the one L2. The arbiter registers each request and passes one down at a time. When both are waiting, the cache that was not served last goes first; neither can be passed over twice. |

`L2_HIT_LATENCY` is the time from a first-level cache's request to the L2's
answer on a hit: one cycle in the arbiter's register and the rest in the
L2. The measured machine has 4. It cannot be 0, and the reason is worth
knowing. A core's fetch request can depend, within one cycle, on whether its
data access was answered (the in-order pipeline holds fetch while the data
port is not ready). If the arbiter passed a request down in the cycle it
arrived, the data side's answer would in turn depend on the fetch request:
a loop with no register in it. The first version of the arbiter did exactly
that, and the simulation of a machine without first-level caches stopped
advancing. With separate memory ports for the two caches the two sides never
met, so the question had not come up before.

Every policy of the cache is available at each level independently:
replacement, what stores do, a write buffer. The lecture's point that
"management policies are different across cache levels" is a matter of
choosing parameters. The benchmark machine uses LRU and write-back at both.

The choices the lectures leave open are made as follows.

- **Split first level, unified second.** Instructions and data share the L2,
  as in lecture 21 ("first level caches are almost always split ... outer
  level caches are almost always unified").
- **Serial access.** The L2 is asked only when a first-level cache misses.
  The lecture's summary of that choice is "slower access to data if previous
  level misses" and "no wasted accesses to next level if previous level
  hits"; with one request at a time on the L2 it is also the only choice
  that fits.
- **The L2 sees only what the first level cannot serve.** A line that is
  used in every cycle from a first-level cache is, to the L2, a line that
  was asked for once and never again. This is the lecture's "previous level
  acts as a filter", and it is what the kernel `incl_victim` is built on.
- **Prefetchers stay at the first level.** A prefetch reaches the L2 the way
  a miss does, as a request for a line.
- **Writes come down as they leave the level above:** a dirty line when the
  data cache evicts it, or the bytes of a store under write-through or
  no-allocate. To the L2 both are writes with strobes, which hit or miss
  and follow its own write policy.

What a data access waits, with nothing else in the way:

| The line is in | Cycles waited | On the measured machine |
| --- | --- | ---: |
| the first-level cache | 0 | 0 |
| its victim buffer | 1 | 1 |
| the L2 | L2 hit latency + 2 | 6 |
| memory only | L2 hit latency + memory latency + 4 | 58 |
| memory only, no L2 | memory latency + 2 | 52 |

The two cycles are the ones every miss of this cache spends before its
request goes out and after the line has arrived; the L2 spends the same two
on its own miss. An inclusive L2 spends two more whenever it has to evict a
line to make room: one asking the first level for it, one coming back. Writing a dirty victim down first costs the L2's latency
plus one more cycle when the L2 has the line and there is no write buffer.
The hierarchy testbench checks the third and fourth rows directly.

### Inclusion

By default the two levels evict independently (`L2_INCLUSION = 0`,
non-inclusive: a line of a first-level cache "may or may not be" in the L2,
in the words of lecture 21). With `L2_INCLUSION = 1` the L2 is inclusive:
every line in a first-level cache, or in its victim buffer, is also in the
L2. The lecture gives the definition and one reason for wanting it
("simplifies cache coherence", which matters once there are several cores);
how to keep the property is not in the lectures. Here it is kept by taking
lines back.

```text
 L2 wants to evict line V
   cycle 1   L2 raises "give up V" to both first-level caches
             a cache that has V drops it; if its copy is dirty it hands it over
             the L2 replaces its own copy with the dirty one and marks it dirty
   cycle 2   L2 evicts V in the usual way (written back if dirty)
```

- **Nothing is lost.** A dirty first-level copy is the only place its data
  exists; it travels down in the same cycle in which the line is dropped.
- **In the cycle in which a cache gives a line up it serves nothing and
  starts nothing.** An access to that line finds it gone in the next cycle
  and is an ordinary miss; there is no cycle in which the line is both
  being used and being taken.
- **Writes already on their way are unaffected.** A store or a dirty line in
  a first-level write buffer, or waiting for its turn at the arbiter, still
  arrives at the L2 afterwards and is applied there as a write that misses.
  One case needed a rule of its own: a write-through store that is already
  waiting for the L2's answer when its line is taken. It finishes when the
  answer comes, without its line, exactly like a store under no-allocate.
- **The victim is taken back, not the line used least by the processor.**
  The L2 chooses its victim from what it has seen, which is only the misses
  from above. The hottest line of the data cache can therefore be the L2's
  least recently used line, and inclusion then removes it from the data
  cache too. That cost is measured in RESULTS.md.

An inclusive cache cannot have a victim buffer of its own (a first-level
cache below an inclusive L2 can).

In simulation the memory subsystem checks the property at the two moments
it could break, when a first-level cache installs a line and when the L2
evicts one, and stops the run if it does not hold.

### Counters and the event log

Each cache counts accesses, misses (and how many of them were stores), lines
evicted, dirty lines written back,
lines read from memory, writes sent to memory, cycles a store waited for a
full write buffer, cycles a request waited for any reason, misses served by
the victim buffer, and lines given up to an inclusive cache below. The
arbiter counts the cycles each first-level cache waited for the other's
turn.

In simulation a cache can also write an event log: one line for every line
it starts to bring in (with the victim it chose, and what that pushed out of
the victim buffer), every swap with the victim buffer, every read and write
on the memory port, every access that completes, every line given up or
taken back. The log holds no timing and no data. `tools/cachesim.py` replays it against a model of the policies
(VERIFICATION.md section 5).

`ENABLE = 0` turns a cache into a wire to memory: every access becomes one
memory transfer and nothing is kept. With a zero-latency memory that
reproduces, cycle for cycle, the ideal memory of Phases 1 and 2. With a real
latency it is the machine without caches that the caches are measured
against.

### Main memory

`tb/sim_memory.sv` transfers whole lines through two independent ports, one
per cache (with an L2 only the second is used), and answers each request a
configurable number of cycles later
(`+mem_latency`). It can add a random extra delay to every transfer
(`+mem_jitter`), which the regression uses to show that results do not
depend on memory timing.

Simplifications, since this is a simulation-only design: a cache hit is
combinational (a real first-level cache would take a cycle or be pipelined),
without an L2 the two caches have separate memory ports instead of sharing
a bus, each cache can have only one line on its way at a time and one
transfer outstanding on its memory port (so the L2 serves one request at a
time), a read is never answered out of the write buffer, and a store that
misses with allocation into the L2 reads the line from memory even when the
write covers all of it. Nothing keeps the instruction cache's copy of a line
up to date when the data cache writes it: a program that modifies its own
code is not supported.

## 7. Commit report and counters

The `commit_*` outputs describe the instruction leaving WB each cycle: its
pc and encoding, and either the register and memory it changed or the trap
it took. The testbench prints this as the commit trace
(VERIFICATION.md section 3). It is the only thing compared with the golden
model, so everything upstream of it is free to change.

The out-of-order core at width 2 can retire two instructions in a cycle and
has a second, smaller commit report for the second one (section 11).

The in-order core's counters explain the cycle count of any run:

```text
cycles = instructions + traps
         + 2 x (mispredictions)
         + 1 x (load-use stalls)
         + 3 x (traps and mrets)
         + cycles fetch waited for the instruction cache
         + cycles the pipeline was held for the data cache
         + a few to fill the pipeline
```

For `conflict` with direct-mapped caches: 27590 + 2 x 3902 + 0 + 0 + 51 +
95104 = 130549, against 130550 measured. The sum is exact when fetch rarely
waits. When it waits often (no instruction cache), a branch that resolves
during a wait squashes bubbles rather than instructions, so the sum
overestimates slightly.

Each cache counts its accesses and its misses, and the data cache what it
exchanges with memory (section 6).
The core counts conditional branches, jumps, and how many of each were
mispredicted. The testbench prints all counters at the end of a run, and the
`bench` stage tabulates them.

## 8. Branch prediction

Fetch must choose the next pc in the same cycle it starts fetching the
current one, before it knows what that instruction is. `bpred.sv` answers
from the pc alone.

![The branch predictor](img/predictor.svg)

```text
                 +--------------------- bpred ----------------------+
   pc ---------> | entry = pc bits      tag match?   counter top bit |--> taken?
                 |   [ valid | tag | target | 2-bit counter ] x 64   |--> target
                 +---------------------------------------------------+
                      ^ train: pc, taken?, target      (from EX)

   next pc = taken ? target : pc + 4
```

| Question fetch needs answered | How the table answers it |
| --- | --- |
| Is there a control transfer at this pc? | An entry whose tag matches |
| Where does it go? | The target stored in the entry |
| Will it be taken this time? | The top bit of the entry's 2-bit counter |

**In the pipeline.** IF looks up the pc and follows the answer. The
predicted next pc is carried along with the instruction. In EX the real next
pc is known for any instruction: the branch or jump target if it transfers
control, `pc + 4` if not. If the two differ, EX redirects fetch to the real
one and squashes the two younger instructions. Nothing else in the pipeline
knows the predictor exists, and a prediction can be arbitrarily wrong
without affecting results; the regression relies on exactly that
(VERIFICATION.md section 5).

**Training** happens in EX, once per instruction, with what really happened:

| Outcome | Entry matches | No matching entry |
| --- | --- | --- |
| Taken | Count up; store the target (it may have changed) | Take the entry over, counter at maximum |
| Not taken | Count down | Nothing |

So a branch that is never taken never occupies an entry, and one table
serves conditional branches, `jal` and `jalr`. For a return, the stored
target is simply where it returned to last time.

**Why two bits.** The counter saturates at 0 and 3 and predicts taken when
it is 2 or 3, so a strong prediction survives one surprise:

```text
   strongly      weakly        weakly        strongly
   not taken <-> not taken <-> taken     <-> taken
       0             1            2             3
   predict not taken          |   predict taken
```

A loop branch is taken many times and then not taken once, at the exit. With
one bit ("do what it did last time") the exit flips the prediction, so the
first iteration of the next run of the loop is mispredicted too: two per
run. With two bits the exit only moves the counter from 3 to 2 and the next
run starts correctly predicted: one per run. `COUNTER_BITS = 1` builds the
one-bit version so that the difference can be measured (RESULTS.md).

A new entry starts at 3. DDCA lecture 15b starts its counter at "weakly
taken" (2); an entry here is created by a taken branch, and 2 plus one taken
outcome is 3, so the two agree. Starting a new entry at 2 instead would have
a cost: a branch that alternates would then bounce between 2 and 1 and be
predicted wrongly every single time, instead of half the time.

**What it cannot do**, by choice of scope: learn
patterns (an alternating branch stays 50% wrong), correlate one branch with
another, or predict the return of a function that has more than one caller.

Simplifications: the lookup is combinational in the fetch cycle, and
branches and jumps are all resolved in EX, including `jal`, whose target
could be known a stage earlier.

## 9. Prefetching

A cache only helps the second time a line is used. A prefetcher tries to
have the line there the first time, by guessing what will be asked for next
and fetching it early.

```text
   processor --- accesses ---> cache <--- line transfers ---> memory
                    |            ^
                    v            | "fetch this line too"
                 prefetcher -----+
```

The prefetcher (`prefetcher.sv`) only watches and proposes. Each cache has
its own. Whether a proposed line is fetched, and when, is decided by the
cache, and a useless proposal can waste time and space but can never change
a result: a prefetched line is just a line that happens to be in the cache.

### Two policies

**Next line.** On an access to line L, propose line L + 1. Right for
anything that moves forward through memory: instruction fetch, or a walk
over an array.

**Stride.** A table indexed by the pc of the load or store keeps, for each
instruction, the last address it used, the step from the one before (the
stride), and a 2-bit confidence.

| On each access by that instruction | Effect |
| --- | --- |
| Same stride as last time | Confidence up (to at most 3) |
| Different stride | Confidence down; once it reaches 0, adopt the new stride |
| Stride repeated and confidence at 2 or more | Propose address + stride x distance |

So two repeats are needed before the first proposal, and one surprise, such
as the jump back to the start of an array at the end of a pass, does not
stop it. A stride of zero proposes nothing. Because the table is per
instruction, two loops walking two arrays with different steps do not
confuse each other, and an instruction with no pattern, like the load that
follows a linked list, proposes nothing at all.

**Distance** is how far ahead the stride prefetcher asks (default 4
strides). A line takes longer to arrive than the processor takes to use
one, so the line needed next is already too late to ask for; the distance
has to cover the memory latency. The next-line prefetcher has no such
setting, and RESULTS.md shows it arriving late for exactly that reason.

### In the cache

The cache treats "the processor missed" and "the prefetcher asked" as the
same job, bringing a line in, started for two different reasons:

1. choose the victim; if it is dirty keep a copy; mark its place empty;
2. write the copy back, if there is one;
3. fetch the wanted line and install it as the most recently used.

Hits are served the whole time. That is essential (a prefetch that stalled
the processor could only hurt) and it is safe because of step 1: the victim
is out of the cache before anything else happens, so no access can hit it,
and no store can change it after its copy was taken.

A prefetch starts only when nothing is on its way, the processor is not
missing in that cycle, the line is not already present, and the victim is
not the line the processor is using in that cycle. Requests that cannot be
served yet simply wait, and a newer proposal replaces an older one.

### Counting

A prefetched line carries a mark until its first use. That gives the three
measures of DDCA lecture 23c:

| Measure | Definition | Says |
| --- | --- | --- |
| Accuracy | prefetched lines later used / prefetches issued | how often the guess was right |
| Coverage | prefetched lines used / (those + remaining misses) | how many misses were removed |
| Timeliness | used prefetches that arrived before they were needed / all used | whether it was early enough |

### Limits

One line can be on its way per cache, and a miss that arrives while a
prefetch is on its way waits behind it. A wrong prefetch therefore costs
time. That is a simplification (a real design would let the miss overtake),
but the effect it exaggerates is real: prefetches compete with real misses
for the memory's bandwidth.

## 10. The out-of-order core

The in-order pipeline executes instructions in the order it fetched them, so
one instruction that has to wait holds up everything behind it, including
work that does not depend on it. `ooo_core.sv` removes that restriction:
instructions still enter and leave in program order, but in between each one
executes as soon as its own operands are ready. The design is Tomasulo's
algorithm with a reorder buffer, as developed in DDCA lectures 13 to 15.

![The out-of-order core](img/core_ooo.svg)

```text
            in program order            out of order            in program order
  fetch --> decode, rename, --> [ reservation stations ] --> ALU --+
            dispatch            [ load/store queue     ] --> mem --+--> common data bus
               |                                                          |
               +--> reorder buffer (one entry per instruction in flight) <-+
                          |
                          v  head, oldest first
                    retire: write the architectural register file, memory, CSRs
```

### The structures

| Structure | Holds | Size (default) |
| --- | --- | ---: |
| Reorder buffer (ROB) | One entry per instruction from dispatch to retirement, in program order: its result, and anything that must wait for retirement (an exception, a misprediction) | 16 |
| Register alias table (RAT) | Per architectural register: is its newest value still being computed, and by which ROB entry | 32 |
| Architectural register file (ARF) | The registers as of the last retired instruction | 32 |
| Reservation stations (RS) | Instructions waiting for the ALU, each with the value of an operand or the tag it is waiting for | 8 |
| Load/store queue (LSQ) | Loads and stores in program order, waiting for address and data | 8 |
| Common data bus (CDB) | One result per cycle, with its tag | 1 (2 at width 2) |

The **tag** of a value is the index of the ROB entry of the instruction that
produces it. That one number links a producer to its consumers: the RAT maps
a register to a tag, a waiting operand remembers a tag, and the bus
announces a tag together with the value. The register file never has to be
searched, and neither does the ROB (DDCA lecture 13's point about using
indirection instead of an associative search).

### The life of an instruction

![The life of an instruction](img/ooo_life.svg)

1. **Dispatch** (one per cycle, in order). It gets the next ROB entry. For
   each source register the RAT is consulted. If the register is not being
   computed, the value is read from the ARF. Otherwise the value is taken
   from the producer's ROB entry if it is already there, or from the bus if
   it is being announced in this very cycle, and failing both the tag is
   kept and the operand waits. Then the RAT entry of the destination
   register is pointed at the new ROB entry: that is register renaming, and
   it is why a later write to the same register cannot disturb an earlier
   reader.
2. **Wait.** Every waiting operand compares the tag on the bus with its own
   each cycle and captures the value when they match.
3. **Execute.** Among the reservation stations with both operands ready, the
   oldest goes to the ALU. Its result is on the bus in the same cycle, so an
   instruction that depends on it can execute in the next one. Branches and
   jumps are resolved here, and a wrong prediction is noted in the ROB
   entry, nothing more.
4. **Complete.** The bus writes the result into the ROB entry and marks it
   done.
5. **Retire** (one per cycle, from the ROB head). The result is written to
   the ARF. If the RAT still names this entry as the producer of that
   register, the register is marked as no longer being computed.

### Memory

Registers can be renamed because the register a value goes to is known at
decode. A store's address is only known when it executes, so loads and
stores need a different discipline (DDCA lecture 15c).

- A **store** computes its address and its data whenever they become ready,
  but writes memory only when it retires. Until then the cache is untouched,
  so a store on a wrong path, or behind an instruction that traps, costs
  nothing to undo.
- A **load** may read the cache once the address of every older store is
  known and none of them overlaps the bytes it reads. If one overlaps, the
  load waits until that store has retired and then reads the cache.

This is the conservative end of the lecture's options: no load ever has to
be re-executed, at the price of making some loads wait when they need not.
There is no store-to-load forwarding and no dependence prediction. Not
forwarding also keeps one subtle thing simple: what a load returns, or
whether it faults, is always decided by the memory system and never by a
value that happened to be sitting in the queue.

### Retirement, exceptions and recovery

Only the instruction at the ROB head can change architectural state, and it
does so only when it retires. That makes exceptions precise with very little
machinery:

| At the head | Action |
| --- | --- |
| Done, no exception | Write the ARF, or for a store write memory; free the entry |
| ... and fetch went the wrong way after it | Also discard everything younger and restart fetch at the right pc |
| Exception | Trap: discard everything, write `mepc`, `mcause`, `mtval`, restart fetch at `mtvec` |

"Discard everything" means emptying the ROB, the reservation stations, the
queue and fetch, and clearing the RAT. Nothing younger than the head has
written the ARF, memory or a CSR, so there is nothing to undo; with the RAT
cleared every register maps to the ARF again, which holds exactly the state
after the last retired instruction. An instruction that faulted on a wrong
path never reaches the head and so never traps.

Mispredictions use the same mechanism: recovery happens when the branch
retires, not when it executes. That is simple and obviously correct, and
costs some cycles on every misprediction (RESULTS.md); recovering at
execution would need a way to restore the RAT to the state at the branch.

**System instructions** (CSR accesses and `mret`) are serialised. One is
dispatched only when the ROB is empty, executes and retires at the head in a
single cycle, and nothing else is dispatched until it has. They are rare,
and this keeps the CSRs strictly in program order with no further logic.

### What limits it

- At width 1, one instruction is dispatched and one retired per cycle, so it
  cannot average more than one instruction per cycle. Section 11 widens it.
- The data cache serves one access at a time and one miss at a time, so
  misses are not overlapped.
- At width 1, one result per cycle on the bus; a load has priority over the
  ALU.
- The window is small. Once the ROB fills behind an instruction that is
  waiting for memory, dispatch stops.

## 11. Two instructions per cycle

Out-of-order execution removes waiting, but a core that takes in one
instruction per cycle and retires one per cycle can never average more than
one. `ooo_core` has a parameter `WIDTH`. At `WIDTH = 1` it is the core of
section 10. At `WIDTH = 2` every stage handles up to two instructions per
cycle, which is what "superscalar" means (DDCA lecture 15a). It is the same
RTL: the second slot of each stage is simply never used at width 1, and the
regression runs both.

Two instructions that travel together are a **pair**; the older one is
always in slot 0. Nearly all of the work in going wide is deciding what the
second of a pair may do in the same cycle as the first.

![The second slot of a two-wide core](img/ooo_pair.svg)

| Stage | Width 1 | Added at width 2 |
| --- | --- | --- |
| Fetch | One instruction, one prediction | The instruction at pc + 4 from the same cache access, and a prediction for it |
| Dispatch | One ROB entry; RAT lookup and update | Two entries; the second sees the first one's register write |
| Execute | Oldest ready station, one bus | The two oldest ready stations, two execution units, two buses |
| Retire | The ROB head | The entry behind the head too, if it needs nothing special |

### Fetching a pair

The instruction cache already returns the 64 bits that start at the address
it is given, so one access yields two instructions whenever both lie in the
same 32-byte line. `mem_subsystem` passes the second one out with a valid
flag; nothing in the cache changed. The predictor is consulted for both
addresses at once (it has a second lookup port on the same table).

The second instruction is taken unless one of two things is true:

- the first is predicted to transfer control, so the instruction after it in
  memory is not the one that follows it;
- the first is the last instruction in its line (one fetch in eight for
  straight-line code).

A taken branch therefore ends a pair, and its target starts a new one. That
is what limits fetch in practice: a loop of five instructions takes three
fetches per iteration, not two and a half.

### Renaming a pair

Both instructions look their sources up in the RAT in the same cycle, but
the RAT does not yet know about the first one. Three cases need care:

| Case | Example | What happens |
| --- | --- | --- |
| The second reads the register the first writes | `addi a0, a0, 1` then `addi a1, a0, 1` | The second ignores the RAT for that operand and waits for the first one's tag (its ROB entry, which is the current tail) |
| Both write the same register | `li a0, 1` then `li a0, 2` | The RAT ends up pointing at the second |
| The second writes the register the first reads | `addi a1, a0, 1` then `li a0, 50` | Nothing special: the first read the RAT before either update |

The second instruction is dispatched with the first only if there is room
for both: two ROB entries, and a second free reservation station or queue
entry if both need the same kind. Otherwise the first goes alone, the second
moves up to slot 0 and goes in the next cycle, and fetch waits one cycle.
A system instruction is still dispatched alone, into an empty ROB, and only
from slot 0.

### Executing two

There are two execution units (`exec_unit.sv`, an ALU and the branch logic)
and two buses. Unit 0 takes the oldest reservation station whose operands
are ready and unit 1 the next oldest. Every waiting operand now compares its
tag with both buses, and so does an operand being looked up at dispatch.

A load that completes needs a bus too. In that cycle the load has one bus,
unit 0 the other, and unit 1 does not fire. There is still one address adder
for the load/store queue and one data cache port, so at most one load
completes per cycle whatever the width.

### Retiring two

The entry behind the ROB head retires in the same cycle as the head if all
of this holds:

- the head retires, and fetch carries on after it (it was not mispredicted
  and is not an `mret`);
- the second entry is done and raised no exception;
- it is not a store and not a system instruction.

So a trap, a store's write to memory and a CSR access still happen only at
the head, one per cycle, exactly as in section 10. That is the reason for
the restriction: everything that makes retirement delicate keeps the
one-at-a-time logic that was already verified, and the second slot only ever
does the easy thing, which is to write a register. An instruction that
cannot retire second simply retires first one cycle later.

The second entry may itself have been mispredicted. Then both retire and
everything younger is discarded, with fetch restarting after the second one.

When both write the same register, the register file keeps the younger
value. Both retirements train the predictor (second training port) and both
count in `minstret`.

### The commit report

The core reports the second retirement of a cycle on a second set of
`commit2_*` outputs: pc, instruction and register write only, since it is
never a trap or a store. The testbench writes the head's line and then the
second one's, so the commit trace is still one instruction per line in
program order and is compared with the golden model exactly as before. If
the head's store halts the machine, the second instruction is not reported:
the program ended at the store.

### What limits it

- Fetch delivers a pair only within a cache line and only up to a predicted
  taken branch.
- A chain of dependent instructions still executes one per cycle; width only
  helps where there is independent work (`dep_chain` against `ilp_chains`
  in RESULTS.md).
- One load completes per cycle and one store retires per cycle.
- A misprediction is still repaired at retirement.
- A pair that cannot be dispatched together costs fetch a cycle; there is no
  queue between fetch and dispatch to absorb it.
