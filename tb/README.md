# Testbenches

A testbench is the code around a design in simulation: it supplies the
clock and the inputs and records or checks the outputs. `core_tb.sv` runs
whole programs on a complete machine. The others each test one block by
itself, for behaviour that no program's result can show, such as which
line a cache replaces.

| File | Tests |
| --- | --- |
| `core_tb.sv` | A whole machine: core, memory subsystem and memory. Writes the commit trace and the caches' event logs, checksums memory at the end |
| `sim_memory.sv` | Main memory model: 64 KiB, whole-line transfers, configurable and random latency |
| `cache_tb.sv` | The cache alone, against a byte array, under random accesses and prefetches |
| `hier_tb.sv` | Both first-level caches, the arbiter and the second level, with both ports driven at once |
| `bpred_tb.sv` | The branch predictor's policy |
| `prefetcher_tb.sv` | The prefetchers' policy |

The testbenches are built and run by `tools/regress.py` (stages `rtl`
and `blocks`), which also compares their output with the golden
model and the cache model. A built core can be run by hand:

```text
vvp -n build/rtl/inorder-dm.vvp +hex=build/golden/tests/sort.hex +mem_latency=10 +trace=build/sort.trace +vcd=build/sort.vcd
```

[docs/VERIFICATION.md](../docs/VERIFICATION.md) describes what each
testbench checks.
