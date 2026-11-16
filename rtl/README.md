# RTL

The processor itself, in SystemVerilog (the subset that `iverilog -g2012`
accepts). RTL, register-transfer level, is the description of hardware as
registers and the logic between them.

The files are listed in the order the project introduced them. The phase
numbers are those of the [top-level README](../README.md).

| Phase | File | What it is |
| ---: | --- | --- |
| 1 | `common/rv_pkg.sv` | Shared definitions: opcodes, exception causes, and the bundle of control signals that travels with each instruction |
| 1 | `common/decoder.sv` | Turns an instruction word into that bundle and checks that the instruction is valid |
| 1 | `common/alu.sv` | Arithmetic, logic and shifts, 64-bit and 32-bit |
| 1 | `common/regfile.sv` | The 32 registers |
| 1 | `inorder/inorder_core.sv` | The five-stage pipeline |
| 2 | `common/csr_file.sv` | The registers that record a trap, and trap entry and return |
| 3 | `common/cache.sv` | The cache. One module is used as instruction cache, data cache and second-level cache |
| 3 | `common/mem_subsystem.sv` | Connects a core to its caches and the caches to memory |
| 4 | `common/bpred.sv` | Branch predictor: a table of branch targets with a 2-bit counter per entry |
| 5 | `common/prefetcher.sv` | Next-line and stride prefetchers |
| 6 | `ooo/ooo_core.sv` | The out-of-order core. Parameter `WIDTH` makes it one or two instructions wide (Phase 7) |
| 6 | `ooo/exec_unit.sv` | Its execution unit: an ALU plus the logic that decides branches |
| 9 | `common/line_arbiter.sv` | Lets the two first-level caches share the second-level cache |

Two properties of the code are used throughout the project:

- **The two cores have the same ports.** The testbench and the memory
  subsystem do not know which core they are connected to, so every test
  runs on both.
- **Every design choice is a parameter, and the default is the simplest
  machine.** Cache size and number of ways, replacement and write policy,
  write buffer, victim buffer, second level and inclusion, predictor size,
  prefetcher type, and the sizes of the out-of-order core's queues can all
  be set per build. This is what allows two machines to differ in exactly
  one thing when they are compared.

[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) explains how each block
works and why it was built that way.
