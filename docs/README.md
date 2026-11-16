# Documentation

The [top-level README](../README.md) goes through the project phase by
phase. These documents hold the detail. A suggested order for a first
reading:

| Order | Document | What it answers |
| ---: | --- | --- |
| 1 | [ARCHITECTURE.md](ARCHITECTURE.md) | How does each block of the processor work, and why was it built that way? |
| 2 | [VERIFICATION.md](VERIFICATION.md) | What machine do the programs run on, and how is every run checked? |
| 3 | [RESULTS.md](RESULTS.md) | What did each phase measure, and where did a component not help? |
| 4 | [SWEEPS.md](SWEEPS.md) | What does each cache parameter do across its range? (generated) |
| 5 | [HISTORY.md](HISTORY.md) | What did each phase build, test and find, and what was planned beyond the last one? |

`img/` holds the charts and diagrams, and `data/` the numbers the charts
are drawn from. Both are generated:

```text
python tools/regress.py bench --update-results   # the tables in RESULTS.md
python tools/regress.py sweep                    # SWEEPS.md, its charts, data/sweeps.json
python tools/figures.py --collect                # the other charts and diagrams, data/project.json
```

A unit test fails if a chart or `SWEEPS.md` is not what the numbers in
`data/` produce, or if a figure in `img/` is not shown in any document.
