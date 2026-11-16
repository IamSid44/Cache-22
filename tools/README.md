# Tools

Everything needed to assemble programs, run them on a reference model,
run the processor in simulation and compare the two. Plain Python 3.10+,
standard library only.

| File | Purpose |
| --- | --- |
| `regress.py` | One-command regression: unit tests, golden-model runs, block testbenches, every core against the golden model, benchmarks, sweeps |
| `rvasm.py` | RV64I + Zicsr assembler |
| `rvsim.py` | Golden model: a simulator that executes one instruction at a time and writes the commit trace, one line per instruction, which every core must reproduce |
| `rvgen.py` | Random program generator. It uses few registers and keeps loads, stores and branches close together, so that instructions interact; it can also insert faults |
| `cachesim.py` | Model of the cache's policies; replays a cache's event log and checks every decision |
| `mutation.py` | Plants bugs in the RTL one at a time and checks that the tests catch each |
| `sweep.py` | Design-space sweeps: fast cache simulators, the optimal replacement policy, miss classification |
| `svgplot.py`, `figures.py` | Charts and diagrams, written as SVG |
| `tests/` | Unit tests of the tools |

```text
python tools/regress.py                    # unit, sw, blocks and rtl stages
python tools/regress.py unit               # only the unit tests
python tools/regress.py bench --update-results
python tools/regress.py sweep              # writes docs/SWEEPS.md and its charts
python tools/figures.py                    # redraws docs/img/ from docs/data/
python tools/mutation.py --check           # confirm no planted bug is left in the RTL

python tools/rvasm.py sw/tests/sort.S -o build/sort.hex --lst build/sort.lst
python tools/rvsim.py sw/tests/sort.S --trace build/sort.trace --regs
python tools/rvgen.py --seed 7 -o build/seed_7.S
python tools/cachesim.py build/sort.dc.log --sets 4 --ways 2 --repl 1
```

Generated files go to `build/`, which is not tracked.
