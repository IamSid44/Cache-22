# Software

The programs the processor runs, in RV64I assembly for the assembler in
`tools/rvasm.py`. Every program checks its own results and reports through
its exit code.

| Folder | Contents |
| --- | --- |
| [`tests/`](tests/) | Directed programs: each targets one area of the instruction set or one mechanism of the cores |
| [`bench/`](bench/) | Benchmark kernels: each makes one component's effect, or its limit, visible in the cycle count |

The platform every program runs on:

| Item | Definition |
| --- | --- |
| Memory | 64 KiB of RAM at address 0, little-endian; the program is loaded at 0 and starts there |
| Halt | A store to `0x1000_0008` ends the run; the stored value is the exit code, 0 for pass |
| Console | A store to `0x1000_0000` prints the low byte |
| Traps | Machine mode only; misaligned or out-of-range accesses and illegal instructions trap |

Run a program on the golden model:

```text
python tools/rvsim.py sw/tests/fib.S
```

The regression runs every program on the golden model and on every core.
